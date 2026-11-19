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
]
KEPT_EDGE = [
    "Senior Engineer", "Engineer", "Analyst", "Product Manager", "Business Analyst", "Test Engineer",
    "System Engineer", "Electrical Engineer", "GTM Engineer", "Product Analyst", "Head of Product",
    "CPU Core Design Verification Testbench Lead", "Safety Engineer (Data)", "Data Partnerships",
    "Data Acquisition Lead", "Delivery Engineer, Frontier Data Products",
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
