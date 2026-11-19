"""The IT-job classifier (pipeline/tech_roles.py). Every title here came from the live feed on 2026-11-19, or from
the LinkedIn list of startups hiring for data the user shared that day. Dropping a real IT job is the worse mistake,
so "other" (the only group that may be dropped) must be clearly non-IT."""

import pytest

from app.pipeline.tech_roles import classify, keep

TECH = [
    "Senior Software Engineer", "Data Analyst", "IT Support Specialist", "Machine Learning Engineer",
    "Cybersecurity Analyst", "Cloud Architect", "Python Developer", "Salesforce Administrator", "Help Desk Technician",
    "Network Engineer", "Forward Deployed Engineer", "Member of Technical Staff", "Founding Engineer",
    "USA_Developer", "Data Center Technician", "Large Language Model Architect", "Integration Engineer",
    "Customer Support Engineer", "Technical Lead", "Security Engineer", "Node.js Developer", "Ruby on Rails Engineer",
    "Director of IT", "Temp to perm :: Unix/Linux Senior Administrator",
    "Junior Information System Security Officer (ISSO)", "Senior Manager, Core Infrastructure Engineering",
    "Senior Frontend Engineer", "Back End Developer", "Member of Data Staff (Analytics Engineer)",
    # the hand-labelled 500 (2026-11-19)
    "TOSCA Quality Engineer", "IBM Tririga Consultant", "Service Reliability Engineer, G&A Solutions Engineering",
    # the second, fresh 500
    "WAS Admin", "Automotive Infotainment", "Quantitative Research Analyst",
    "eDiscovery Litigation Technologist (Top Secret Clearance Required)",
]
KEPT_EDGE = [
    "Senior Engineer", "Engineer", "Analyst", "Product Manager", "Business Analyst", "Test Engineer",
    "System Engineer", "Electrical Engineer", "GTM Engineer", "Product Analyst", "Head of Product",
    "CPU Core Design Verification Testbench Lead", "Safety Engineer (Data)", "Data Partnerships",
    "Data Acquisition Lead", "Delivery Engineer, Frontier Data Products", "Senior Robotics Engineer, Data Collection",
]
OTHER = [
    "Data Entry Clerk", "Security Officer Armed Patrol", "Target Security Specialist", "Store Associate",
    "Mechanical Engineer", "Registered Nurse", "Maintenance Technician", "Paramedic", "QA Inspector",
    "Senior Accountant", "HVAC Technician", "Waiter", "Ruby Tuesday Server", "Make it happen Associate",
    "Financial Analyst", "CT Technologist", "Product/Event Demonstrator", "Project Engineer", "Quality Engineer",
    "Graphic Designer", "Production Supervisor", "Field Service Engineer", "Technical Recruiter",
    "Board Certified Behavior Analyst (BCBA)", "Supply Chain Analyst", "Mapping Data Collection Driver",
    "Security Officer Patrol", "Daytime Cashier/ Front End Ambassator",
    "Associate Director - Project Controls | Defence & Infrastructure", "Semiconductor Equipment Engineer - 2nd Shift",
]


@pytest.mark.parametrize("title", TECH)
def test_clear_it_roles(title):
    assert classify(title) == "tech"


@pytest.mark.parametrize("title", KEPT_EDGE)
def test_could_be_it_is_kept(title):
    assert classify(title) == "edge" and keep(title)


@pytest.mark.parametrize("title", OTHER)
def test_clearly_not_it(title):
    assert classify(title) == "other" and not keep(title)


def test_the_description_rescues_a_vague_title_but_not_a_lone_tool():
    from app.pipeline.tech_roles import classify_job

    assert classify_job("Consultant", "You will build data pipelines in Python and SQL on AWS.") == "edge"
    assert classify_job("Financial Analyst", "Advanced Excel; SQL a plus.") == "other"
    assert classify_job("Security Officer", "Python AWS Kubernetes") == "other"  # never for clearly non-IT titles
    assert classify_job("Vehicle Program Role", "You will write Python tools for our team") == "edge"  # one strong
    assert classify_job("Store Associate", "Use our POS software; git along with the team; API production") == "other"
