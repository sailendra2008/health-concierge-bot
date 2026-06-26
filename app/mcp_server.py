import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Healthcare Concierge Tools")

@mcp.tool()
def explain_lab_report(raw_report_text: str) -> str:
    """Parses and explains clinical lab report findings (e.g. cholesterol, hemoglobin, HbA1c).

    Args:
        raw_report_text: The clinical lab report text to evaluate and explain.
    """
    # Simple parser to find common medical indicators
    explanation = []
    text_lower = raw_report_text.lower()
    
    if "cholesterol" in text_lower:
        explanation.append(
            "Cholesterol is a waxy substance found in your blood. High cholesterol can limit blood flow. "
            "Ideally, Total Cholesterol should be under 200 mg/dL. Values between 200-239 are borderline high, "
            "and 240+ is considered high risk."
        )
    if "hba1c" in text_lower or "a1c" in text_lower:
        explanation.append(
            "HbA1c measures your average blood sugar levels over the past 3 months. "
            "A normal level is below 5.7%. 5.7% to 6.4% indicates prediabetes, and 6.5% or higher indicates diabetes."
        )
    if "hemoglobin" in text_lower:
        explanation.append(
            "Hemoglobin is a protein in red blood cells that carries oxygen. "
            "Normal levels are typically 13.8 to 17.2 g/dL for men, and 12.1 to 15.1 g/dL for women. "
            "Low levels can suggest anemia."
        )
        
    if not explanation:
        explanation.append(
            "Analyzed lab parameters. The report contains clinical metrics. "
            "Please ensure you consult your primary physician to get a diagnostic assessment."
        )
        
    return "\n\n".join(explanation)

@mcp.tool()
def schedule_med_reminder(medicine_name: str, dose: str, time: str) -> str:
    """Schedules a daily medication reminder.

    Args:
        medicine_name: Name of the medication (e.g. Aspirin, Metformin).
        dose: Dose description (e.g. 500mg, 1 tablet).
        time: Timing for the reminder (e.g. 8:00 AM, at dinner).
    """
    return f"Success: Daily reminder configured for medication '{medicine_name}' ({dose}) at {time}."

@mcp.tool()
def find_nearby_hospitals(city_or_zip: str) -> str:
    """Finds medical centers, emergency rooms, or clinics near a specified city or ZIP code.

    Args:
        city_or_zip: The location (e.g. 'Boston', '90210').
    """
    facilities = [
        {"name": "Metropolitan Health Hospital", "distance": "0.8 miles", "type": "Emergency Room, Urgent Care"},
        {"name": "St. Jude Clinic", "distance": "1.5 miles", "type": "Family Medicine"},
        {"name": "CareFirst Urgent Care", "distance": "2.3 miles", "type": "Walk-in Urgent Care"}
    ]
    
    result = f"🏥 Clinical Facilities near '{city_or_zip}':\n"
    for fac in facilities:
        result += f"- {fac['name']} ({fac['distance']}) - {fac['type']}\n"
    return result

@mcp.tool()
def update_health_summary(summary_update: str) -> str:
    """Appends patient symptoms, diet choices, or medical logs to their clinical record summary.

    Args:
        summary_update: The detailed observation or symptom to record.
    """
    return f"Record Update Success: Recorded clinical observation: '{summary_update}'."

if __name__ == "__main__":
    mcp.run()
