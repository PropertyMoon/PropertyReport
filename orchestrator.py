"""
Property Research Agent - Orchestrator
Uses Claude API with web search to research Australian properties
"""

import anthropic
import json
from dataclasses import dataclass


@dataclass
class PropertyReport:
    address: str
    suburb: dict
    schools: dict
    government_projects: dict
    transport: dict
    property_market: dict
    risk_overlays: dict
    summary: str


# ─── Research Prompts ────────────────────────────────────────────────────────

RESEARCH_TASKS = {
    "suburb": """
Research the suburb profile for the property at: {address}

Find and return detailed information on:
1. Suburb name and postcode
2. Median house and unit prices (current + 1yr, 5yr growth trend)
3. Rental yield estimates
4. Population and demographic overview (family-friendly, young professionals, etc.)
5. Crime statistics for this suburb (reference crimestats.vic.gov.au if possible)
6. Key amenities (shopping centres, hospitals, parks, restaurants)
7. Overall liveability rating and what makes this suburb attractive

Be specific with numbers and cite sources where possible.
Format your response as structured JSON.
""",

    "schools": """
Research schools near this property address: {address}

Find and return:
1. All public primary schools within 3km (name, distance, ICSEA score, rating)
2. All public secondary schools within 5km (name, distance, ICSEA score, rating)
3. Notable private schools in the area (name, distance, fees range if available)
4. Whether the property falls within a sought-after school catchment zone
5. Overall school quality assessment for this area

Use myschool.edu.au data where possible.
Format your response as structured JSON.
""",

    "government_projects": """
Research planned and current government infrastructure projects near: {address}

Find and return:
1. State government transport projects (rail, road, tram extensions)
2. Federal government investment in the area
3. Council-approved developments (new parks, community centres, libraries)
4. Zoning changes or urban renewal plans
5. Any major upcoming projects that could affect property value positively or negatively
6. Timeline for key projects

Check planning.vic.gov.au, infrastructure.vic.gov.au and local council websites.
Format your response as structured JSON.
""",

    "transport": """
Research transport and connectivity for: {address}

Find and return:
1. Nearest train station (name, distance, line, travel time to Melbourne CBD)
2. Bus routes available nearby
3. Tram access if applicable
4. Drive time to Melbourne CBD (peak hour and off-peak)
5. Drive time to nearest major shopping centre
6. Walkability assessment (can daily errands be done on foot?)
7. Cycling infrastructure nearby

Format your response as structured JSON.
""",

    "property_market": """
Research the property market conditions for: {address}

Find and return:
1. Recent comparable sales in the same street or suburb (last 6 months)
2. Average days on market for this suburb
3. Auction clearance rates in this area
4. Current supply vs demand indicators
5. Price per square metre benchmarks
6. Best streets or pockets within this suburb
7. Market outlook for this suburb (growth potential)

Use realestate.com.au and domain.com.au data where possible.
Format your response as structured JSON.
""",

    "risk_overlays": """
Research planning overlays and risk factors for: {address}

Find and return:
1. Flood risk zone (check vicfloodmap or council flood maps)
2. Bushfire risk rating (BAL rating if applicable)
3. Heritage overlay restrictions
4. Significant landscape overlay
5. Development potential (can subdivide? build up?)
6. Any noise or flight path concerns
7. Environmental contamination flags if any

Check planning.vic.gov.au planning maps.
Format your response as structured JSON.
"""
}


# ─── Individual Research Agent ────────────────────────────────────────────────

def run_research_task(client: anthropic.Anthropic, task_name: str, address: str) -> dict:
    """Run a single research task using Claude with web search."""
    
    print(f"  🔍 Researching {task_name}...")
    
    prompt = RESEARCH_TASKS[task_name].format(address=address)
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        system="""You are an expert Australian property researcher specialising in Melbourne and Victoria.
Your job is to research specific aspects of a property and return accurate, structured data.
Always respond with valid JSON only — no markdown, no preamble, no explanation outside the JSON.
If you cannot find specific data, include the field with a null value and a "note" explaining why.
Be thorough and use web search to find current, accurate information.""",
        messages=[{"role": "user", "content": prompt}]
    )
    
    # Extract text from response (may include tool use blocks)
    full_text = ""
    for block in response.content:
        if block.type == "text":
            full_text += block.text
    
    # Parse JSON response
    try:
        # Strip markdown fences if present
        clean = full_text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        # Return raw text if JSON parsing fails
        print(f"  ⚠️  Could not parse JSON for {task_name}, storing raw text")
        return {"raw_text": full_text, "parse_error": True}


# ─── Synthesis Agent ─────────────────────────────────────────────────────────

def synthesise_report(client: anthropic.Anthropic, address: str, research_data: dict) -> str:
    """Take all research data and synthesise into a buyer-friendly narrative report."""
    
    print("  ✍️  Synthesising final report...")
    
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        system="""You are a senior property analyst writing detailed investment reports for Australian property buyers.
Write in a clear, professional but accessible tone. Be honest about both positives and negatives.
Structure the report clearly with sections. Use specific data points from the research provided.
The goal is to help buyers make an informed decision about purchasing this property.""",
        messages=[{
            "role": "user",
            "content": f"""Using the research data below, write a comprehensive property report for:

ADDRESS: {address}

RESEARCH DATA:
{json.dumps(research_data, indent=2)}

Write a detailed report with these sections:
1. Executive Summary (3-4 sentences, verdict on the property)
2. Suburb Profile & Liveability
3. School Catchments & Education
4. Infrastructure & Government Investment
5. Transport & Connectivity
6. Property Market Analysis
7. Risk Assessment
8. Investment Verdict & Recommendation

Be specific with numbers. Highlight key strengths and flag any concerns clearly."""
        }]
    )
    
    return response.content[0].text


# ─── Main Orchestrator ────────────────────────────────────────────────────────

def research_property(address: str, api_key: str = None) -> PropertyReport:
    """
    Main orchestrator function. 
    Runs all research tasks and synthesises into a full report.
    
    Args:
        address: Full Australian property address (e.g. "123 Smith St, Richmond VIC 3121")
        api_key: Anthropic API key (or set ANTHROPIC_API_KEY env var)
    
    Returns:
        PropertyReport dataclass with all research data and final report
    """
    
    # Initialise client
    if api_key:
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var
    
    print(f"\n🏠 Starting property research for: {address}")
    print("=" * 60)
    
    # Run all research tasks
    research_data = {}
    
    for task_name in RESEARCH_TASKS.keys():
        try:
            research_data[task_name] = run_research_task(client, task_name, address)
        except Exception as e:
            print(f"  ❌ Error in {task_name}: {e}")
            research_data[task_name] = {"error": str(e)}
    
    print("\n📝 All research complete. Generating report...")
    print("=" * 60)
    
    # Synthesise full narrative report
    summary = synthesise_report(client, address, research_data)
    
    # Build and return report object
    report = PropertyReport(
        address=address,
        suburb=research_data.get("suburb", {}),
        schools=research_data.get("schools", {}),
        government_projects=research_data.get("government_projects", {}),
        transport=research_data.get("transport", {}),
        property_market=research_data.get("property_market", {}),
        risk_overlays=research_data.get("risk_overlays", {}),
        summary=summary
    )
    
    print("\n✅ Report complete!")
    return report


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python orchestrator.py \"123 Smith St, Richmond VIC 3121\"")
        sys.exit(1)
    
    address = " ".join(sys.argv[1:])
    report = research_property(address)
    
    print("\n" + "=" * 60)
    print("FULL REPORT")
    print("=" * 60)
    print(report.summary)
    
    # Save raw data for debugging / PDF generation later
    output = {
        "address": report.address,
        "research_data": {
            "suburb": report.suburb,
            "schools": report.schools,
            "government_projects": report.government_projects,
            "transport": report.transport,
            "property_market": report.property_market,
            "risk_overlays": report.risk_overlays,
        },
        "summary": report.summary
    }
    
    with open("report_output.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print("\n📁 Raw data saved to report_output.json")
