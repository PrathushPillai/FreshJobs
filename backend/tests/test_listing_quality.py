import pytest

from app.pipeline.listing_quality import listing_flag


@pytest.mark.parametrize("text,flag", [
    ("You will need to purchase your own starter kit before training begins.", "scam"),
    ("We will send you a check to purchase your home office equipment.", "scam"),
    ("Deposit the check and send the remainder to our vendor.", "scam"),
    ("Package forwarding assistant, work from home.", "scam"),
    ("Interviews are held on Telegram with the hiring manager.", "scam"),
    ("Contact us on WhatsApp to schedule.", "scam"),
    ("Be your own boss with unlimited earning potential. 100% commission.", "mlm"),
    ("Network marketing opportunity with residual income.", "mlm"),
    # Honest jobs that share a word with the rules.
    ("Base salary plus commission. Sales experience preferred.", None),
    ("We use Signal and Slack for chat. Competitive salary.", None),
    ("Software engineer working on payment processing systems.", None),
    ("Be your own boss of the roadmap: you own the product.", None),
    ("We reimburse your home office equipment.", None),
])
def test_listing_flag(text, flag):
    assert listing_flag("A job", text) == flag
