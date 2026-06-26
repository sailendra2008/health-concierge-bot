import os
import re
import json
import logging
from typing import Any, Literal
from pydantic import BaseModel, Field

from google.adk import Workflow
from google.adk.workflow import node, Edge, START
from google.adk.agents import LlmAgent, Context
from google.adk.models import Gemini
from google.adk.tools import AgentTool, McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from mcp import StdioServerParameters
from google.adk.events import RequestInput, Event
from google.adk.apps import App

from app.config import config

# Set up logger
logger = logging.getLogger(__name__)

# State schema for the Healthcare Concierge Workflow
class HealthcareState(BaseModel):
    user_query: str = ""
    patient_name: str = "Anonymous Patient"
    consent_given: bool = False
    pending_reminder: dict[str, str] = Field(default_factory=dict)
    health_summary: str = "No current updates."
    audit_trail: list[str] = Field(default_factory=list)

@node(name="security_checkpoint", rerun_on_resume=True)
def security_checkpoint(ctx: Context, node_input: Any) -> Any:
    """Security Checkpoint: Checks for prompt injections, scrubs PII, and verifies patient consent."""
    # On resume, reuse previously stored query from state to avoid re-parsing Content
    query = ctx.state.get("user_query", "")
    if not query:
        # First run: extract from node_input
        if isinstance(node_input, str):
            query = node_input
        elif isinstance(node_input, dict) and "query" in node_input:
            query = node_input["query"]
        elif hasattr(node_input, "parts") and node_input.parts:
            # google.genai Content object
            query = " ".join(p.text for p in node_input.parts if hasattr(p, "text") and p.text)
        elif hasattr(node_input, "text") and node_input.text:
            query = node_input.text
        else:
            query = str(node_input)
        ctx.state["user_query"] = query

    # 1. Prompt Injection Detection
    injection_keywords = [
        "ignore previous instructions", 
        "system prompt", 
        "bypass security", 
        "jailbreak", 
        "override instructions",
        "forget what I said"
    ]
    for keyword in injection_keywords:
        if keyword in query.lower():
            audit_log = {
                "event": "security_checkpoint", 
                "status": "REJECTED", 
                "reason": f"Prompt injection keyword detected: {keyword}", 
                "severity": "CRITICAL"
            }
            logger.error(json.dumps(audit_log))
            ctx.state.setdefault("audit_trail", []).append(json.dumps(audit_log))
            ctx.route = "SECURITY_EVENT"
            return "Security violation: Prompt injection attempt detected. Access denied."

    # 2. PII Scrubbing
    scrubbed_query = query
    # Email scrubbing
    scrubbed_query = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL_REDACTED]", scrubbed_query)
    # Phone number scrubbing
    scrubbed_query = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE_REDACTED]", scrubbed_query)
    # SSN / Medical ID scrubbing
    scrubbed_query = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN_REDACTED]", scrubbed_query)
    scrubbed_query = re.sub(r"\bMRN\d{6,10}\b", "[MRN_REDACTED]", scrubbed_query)

    ctx.state["user_query"] = scrubbed_query

    # 3. Domain-Specific Rule: Patient Consent Check
    sensitive_request = any(kw in query.lower() for kw in ["reminder", "schedule", "lab report", "cholesterol", "hba1c", "blood test", "summary", "record"])
    if sensitive_request and not ctx.state.get("consent_given", False):
        consent_response = ctx.resume_inputs.get("patient_consent")
        if consent_response is not None:
            if str(consent_response).lower().strip() in ["yes", "y", "true", "consent"]:
                ctx.state["consent_given"] = True
                audit_log = {
                    "event": "security_checkpoint", 
                    "status": "APPROVED", 
                    "reason": "Patient consent provided on prompt", 
                    "severity": "INFO"
                }
                logger.info(json.dumps(audit_log))
                ctx.state.setdefault("audit_trail", []).append(json.dumps(audit_log))
            else:
                audit_log = {
                    "event": "security_checkpoint", 
                    "status": "REJECTED", 
                    "reason": "Patient denied consent", 
                    "severity": "WARNING"
                }
                logger.warning(json.dumps(audit_log))
                ctx.state.setdefault("audit_trail", []).append(json.dumps(audit_log))
                ctx.route = "SECURITY_EVENT"
                return "I cannot view your medical information or schedule reminders without your consent."
        else:
            audit_log = {
                "event": "security_checkpoint", 
                "status": "INTERRUPT", 
                "reason": "Awaiting patient consent", 
                "severity": "WARNING"
            }
            logger.warning(json.dumps(audit_log))
            ctx.state.setdefault("audit_trail", []).append(json.dumps(audit_log))
            return RequestInput(
                interrupt_id="patient_consent",
                message="🏥 Consent Request: To view lab reports, schedule reminders, or access health summaries, please confirm your consent. Do you consent? (yes/no)",
                response_schema=str
            )

    audit_log = {"event": "security_checkpoint", "status": "PASSED", "severity": "INFO"}
    logger.info(json.dumps(audit_log))
    ctx.state.setdefault("audit_trail", []).append(json.dumps(audit_log))
    ctx.route = "DEFAULT_ROUTE"
    return scrubbed_query

# MCP server connection details - create separate toolset per agent to avoid shared subprocess
def make_mcp_toolset() -> McpToolset:
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=["run", "python", "-m", "app.mcp_server"]
            )
        )
    )

mcp_toolset_explainer = make_mcp_toolset()
mcp_toolset_reminder = make_mcp_toolset()

# Sub-Agent 1: Lab Report Explainer
medical_explainer_agent = LlmAgent(
    name="medical_explainer_agent",
    model=Gemini(model=config.model),
    instruction="""You are a specialized Medical Lab Report Explainer for a Healthcare Concierge app.

Your job:
- Explain lab results, blood test values, or health terms — whether the user pastes a full report OR just mentions individual values inline (e.g., "my cholesterol is 240", "HbA1c is 7.2", "WBC is 11,000").
- For EACH value mentioned, explain: what it measures, the normal reference range, what the user's value means (normal/high/low/borderline), and actionable lifestyle tips.
- Use plain language — no jargon. Use emojis for readability (e.g., ✅ normal, ⚠️ borderline, 🔴 high).
- Format your response clearly with headings for each test value.
- Always end with: "⚕️ I am an AI assistant, not a doctor. Please consult your physician for personalized medical advice."

Example response format for cholesterol 240 mg/dL:
📊 **Cholesterol: 240 mg/dL** — 🔴 High (Normal: <200 mg/dL)
> Your total cholesterol is above the desirable range. This may increase cardiovascular risk. Tips: reduce saturated fats, increase exercise, eat more fiber.

Do NOT ask the user to upload a formal report if they have already given you values — explain them immediately.""",
    tools=[mcp_toolset_explainer]
)

# Sub-Agent 2: Medication Reminder Assistant
reminder_agent = LlmAgent(
    name="reminder_agent",
    model=Gemini(model=config.model),
    instruction="""You are a specialized Medication Reminder Assistant for a Healthcare Concierge app.

Your job:
- Help users schedule medication reminders. Extract the medicine name, dose, and time from natural language (e.g., "remind me to take Metformin 500mg at 8am and 8pm").
- Use the `schedule_med_reminder` tool to schedule each reminder.
- After scheduling, confirm with a friendly summary: list each medicine, dose, and time in a clear format (use 💊 emoji).
- If the user mentions updating their health summary or adding a condition, use `update_health_record`.
- Keep replies warm and supportive.

Example response after scheduling:
💊 Reminder Set Successfully!
- Metformin 500mg — ⏰ 8:00 AM daily
- Metformin 500mg — ⏰ 8:00 PM daily
"You're all set! I'll remind you to take your medication on time.""",
    tools=[mcp_toolset_reminder]
)

# Root Orchestrator Agent
orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Healthcare Concierge — a warm, professional AI medical assistant.

Your capabilities:
🔬 **Lab Report Explanations** — explain any blood test or lab value
⏰ **Medicine Reminders** — schedule daily medication reminders
❓ **Health Q&A** — answer general health questions (non-diagnostic)
🏥 **Find Hospitals** — help locate nearby hospitals or clinics
📋 **Health Summary** — maintain the patient's health record summary

Routing rules:
- For ANY lab result, blood test value, or health term explanation → delegate to `medical_explainer_agent`.
  Examples: "my cholesterol is 240", "explain my HbA1c of 7.2", "what does WBC mean", "can you explain my blood report?"
- For scheduling reminders or updating records → delegate to `reminder_agent`.
  Examples: "remind me to take Metformin at 8am", "schedule my insulin reminder"

Important:
- If the user's query contains specific lab values (numbers + test names), ALWAYS delegate to `medical_explainer_agent` immediately — pass the exact values to it.
- Do NOT make the user repeat themselves. Pass their original message to the sub-agent as context.
- After the sub-agent responds, present the result cleanly to the user.
- Keep your tone friendly, empathetic, and professional.
- NEVER diagnose. Always recommend consulting a physician for clinical decisions.""",
    tools=[AgentTool(medical_explainer_agent), AgentTool(reminder_agent)]
)

@node(name="post_orchestration")
def post_orchestration_node(ctx: Context, node_input: Any) -> Any:
    """Post-Orchestration Node: Inspects session events for pending reminders and routes to HITL approval if needed."""
    has_pending_reminder = False
    
    # Safely scan session events for tool invocations
    for event in ctx._invocation_context.session.events:
        if event.content and hasattr(event.content, "parts") and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    if fc.name == "schedule_med_reminder":
                        has_pending_reminder = True
                        args = fc.args if hasattr(fc, "args") else {}
                        ctx.state["pending_reminder"] = {
                            "medicine_name": args.get("medicine_name", "Medication"),
                            "dose": args.get("dose", "1 dose"),
                            "time": args.get("time", "As directed")
                        }
                        break

    if has_pending_reminder and not ctx.state.get("reminder_approved", False):
        ctx.route = "NEEDS_APPROVAL"
        return "A medication reminder requires confirmation."
    
    ctx.route = "DEFAULT_ROUTE"
    return node_input

@node(name="human_verification", rerun_on_resume=True)
def human_verification_node(ctx: Context, node_input: Any) -> Any:
    """Human-in-the-loop verification node using RequestInput."""
    reminder = ctx.state.get("pending_reminder", {})
    if not reminder:
        return "No pending reminder to verify."
    
    confirm_response = ctx.resume_inputs.get("reminder_confirm")
    if confirm_response is not None:
        if str(confirm_response).lower().strip() in ["yes", "y", "approve", "confirm"]:
            ctx.state["reminder_approved"] = True
            # Clear pending reminder state after success
            ctx.state["pending_reminder"] = {}
            audit_log = {
                "event": "human_verification", 
                "status": "APPROVED", 
                "reminder": reminder, 
                "severity": "INFO"
            }
            ctx.state.setdefault("audit_trail", []).append(json.dumps(audit_log))
            return f"✅ Approved! Daily reminder for {reminder.get('medicine_name')} ({reminder.get('dose')}) at {reminder.get('time')} is now active."
        else:
            ctx.state["pending_reminder"] = {}
            audit_log = {
                "event": "human_verification", 
                "status": "DENIED", 
                "reminder": reminder, 
                "severity": "WARNING"
            }
            ctx.state.setdefault("audit_trail", []).append(json.dumps(audit_log))
            return "❌ Denied. The reminder has not been scheduled."

    # Return request input to prompt the user
    return RequestInput(
        interrupt_id="reminder_confirm",
        message=f"✋ Patient Confirmation Needed: Please confirm you want to schedule {reminder.get('medicine_name')} ({reminder.get('dose')}) at {reminder.get('time')}. (Reply yes/no)",
        response_schema=str
    )

@node(name="security_alert")
def security_alert_node(ctx: Context, node_input: Any) -> Any:
    """Security Alert terminal node."""
    if isinstance(node_input, str):
        return node_input
    return "🚨 Security event detected. Your request has been blocked."

@node(name="final_output")
def final_output_node(ctx: Context, node_input: Any) -> Any:
    """Final output formatter - extracts text from Content objects or passes strings through."""
    # If it's already a string, return it directly
    if isinstance(node_input, str):
        return node_input
    # Extract text from google.genai Content / Parts structure
    if hasattr(node_input, "parts") and node_input.parts:
        texts = [p.text for p in node_input.parts if hasattr(p, "text") and p.text]
        if texts:
            return "\n".join(texts)
    # Fallback: try last model event text from session
    try:
        for event in reversed(ctx._invocation_context.session.events):
            if event.content and hasattr(event.content, "parts"):
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        return part.text
    except Exception:
        pass
    return str(node_input)

# Healthcare Concierge workflow definition
workflow = Workflow(
    name="healthcare_concierge_workflow",
    description="Secure, multi-agent healthcare concierge workflow.",
    state_schema=HealthcareState,
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {
            "SECURITY_EVENT": security_alert_node,
            "DEFAULT_ROUTE": orchestrator_agent
        }),
        (orchestrator_agent, post_orchestration_node),
        (post_orchestration_node, {
            "NEEDS_APPROVAL": human_verification_node,
            "DEFAULT_ROUTE": final_output_node
        }),
        (human_verification_node, final_output_node),
        (security_alert_node, final_output_node)
    ]
)

# Export the application
app = App(
    root_agent=workflow,
    name="app",
)
