from app.sources.adp import ADPAdapter
from app.sources.adzuna import AdzunaAdapter
from app.sources.amazon import AmazonAdapter
from app.sources.apple import AppleAdapter
from app.sources.ashby import AshbyAdapter
from app.sources.avature import AvatureAdapter
from app.sources.bamboohr import BambooHRAdapter
from app.sources.base import Adapter, NormalizedJob
from app.sources.breezy import BreezyAdapter
from app.sources.bullhorn import BullhornAdapter
from app.sources.eightfold import EightfoldAdapter
from app.sources.gem import GemAdapter
from app.sources.google import GoogleAdapter
from app.sources.greenhouse import GreenhouseAdapter
from app.sources.hackernews import HackerNewsAdapter
from app.sources.icims import ICIMSAdapter
from app.sources.jazzhr import JazzHRAdapter
from app.sources.jobdiva import JobDivaAdapter
from app.sources.jobvite import JobviteAdapter
from app.sources.jsearch import JSearchAdapter
from app.sources.lever import LeverAdapter
from app.sources.manatal import ManatalAdapter
from app.sources.microsoft import MicrosoftAdapter
from app.sources.oracle_hcm import OracleHCMAdapter
from app.sources.paylocity import PaylocityAdapter
from app.sources.pinpoint import PinpointAdapter
from app.sources.polymer import PolymerAdapter
from app.sources.radancy import RadancyAdapter
from app.sources.recruitee import RecruiteeAdapter
from app.sources.remoteok import RemoteOKAdapter
from app.sources.remotive import RemotiveAdapter
from app.sources.rippling import RipplingAdapter
from app.sources.simplify import SimplifyAdapter
from app.sources.smartrecruiters import SmartRecruitersAdapter
from app.sources.successfactors import SuccessFactorsAdapter
from app.sources.taleo import TaleoAdapter
from app.sources.themuse import TheMuseAdapter
from app.sources.workable import WorkableAdapter
from app.sources.workable_search import WorkableSearchAdapter
from app.sources.website import WebsiteAdapter
from app.sources.workday import WorkdayAdapter
from app.sources.yc import YCAdapter
from app.sources.zoho import ZohoRecruitAdapter

ADAPTERS: dict[str, Adapter] = {
    a.ats: a
    for a in (
        GreenhouseAdapter(),
        LeverAdapter(),
        AshbyAdapter(),
        WorkdayAdapter(),
        SmartRecruitersAdapter(),
        WorkableAdapter(),
        WorkableSearchAdapter(),
        AmazonAdapter(),
        MicrosoftAdapter(),
        GoogleAdapter(),
        AppleAdapter(),
        OracleHCMAdapter(),
        ICIMSAdapter(),
        EightfoldAdapter(),
        SuccessFactorsAdapter(),
        RipplingAdapter(),
        BambooHRAdapter(),
        RecruiteeAdapter(),
        BreezyAdapter(),
        PinpointAdapter(),
        GemAdapter(),
        PolymerAdapter(),
        JazzHRAdapter(),
        RadancyAdapter(),  # TalentBrew careers sites (Spectrum, Moody's, Arm...), from their sitemaps
        AvatureAdapter(),  # Avature portals (Koch, Bloomberg, Synopsys...), from their sitemaps
        TaleoAdapter(),  # Oracle Taleo Enterprise career sections (Textron, D.R. Horton, Zions...)
        ADPAdapter(),  # ADP Workforce Now career centers
        PaylocityAdapter(),  # Paylocity job boards
        JobviteAdapter(),  # jobs.jobvite.com career sites
        # Staffing and IT-consulting ATSs (much of LinkedIn's IT volume): the employer shown is the firm.
        JobDivaAdapter(),
        BullhornAdapter(),
        ZohoRecruitAdapter(),
        ManatalAdapter(),
        SimplifyAdapter(),
        YCAdapter(),
        HackerNewsAdapter(),
        WebsiteAdapter(),
        TheMuseAdapter(),
        RemotiveAdapter(),
        RemoteOKAdapter(),
        AdzunaAdapter(),
        JSearchAdapter(),
    )
}

__all__ = ["ADAPTERS", "Adapter", "NormalizedJob"]
