from app.sources.adzuna import AdzunaAdapter
from app.sources.amazon import AmazonAdapter
from app.sources.apple import AppleAdapter
from app.sources.ashby import AshbyAdapter
from app.sources.base import Adapter, NormalizedJob
from app.sources.eightfold import EightfoldAdapter
from app.sources.google import GoogleAdapter
from app.sources.greenhouse import GreenhouseAdapter
from app.sources.jsearch import JSearchAdapter
from app.sources.icims import ICIMSAdapter
from app.sources.lever import LeverAdapter
from app.sources.microsoft import MicrosoftAdapter
from app.sources.oracle_hcm import OracleHCMAdapter
from app.sources.remoteok import RemoteOKAdapter
from app.sources.remotive import RemotiveAdapter
from app.sources.smartrecruiters import SmartRecruitersAdapter
from app.sources.successfactors import SuccessFactorsAdapter
from app.sources.themuse import TheMuseAdapter
from app.sources.workable import WorkableAdapter
from app.sources.workday import WorkdayAdapter

ADAPTERS: dict[str, Adapter] = {
    a.ats: a
    for a in (
        GreenhouseAdapter(),
        LeverAdapter(),
        AshbyAdapter(),
        WorkdayAdapter(),
        SmartRecruitersAdapter(),
        WorkableAdapter(),
        AmazonAdapter(),
        MicrosoftAdapter(),
        GoogleAdapter(),
        AppleAdapter(),
        OracleHCMAdapter(),
        ICIMSAdapter(),
        EightfoldAdapter(),
        SuccessFactorsAdapter(),
        TheMuseAdapter(),
        RemotiveAdapter(),
        RemoteOKAdapter(),
        AdzunaAdapter(),
        JSearchAdapter(),
    )
}

__all__ = ["ADAPTERS", "Adapter", "NormalizedJob"]
