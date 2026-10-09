"""ORM model exports."""

from app.models.api_key import ApiKey
from app.models.audit import AuditLog
from app.models.base import Base, metadata
from app.models.billing import (
    BillingContact,
    BillingInvoice,
    BillingPaymentMethod,
    BillingSubscription,
)
from app.models.connector_mapping import ConnectorMapping
from app.models.device import Device, DeviceEvent, DeviceGrant, MirrorSession
from app.models.experiment import Experiment, ExperimentFold
from app.models.feature import FeatureDefinition
from app.models.forecast import Calibration, Forecast, ForecastExplanation, ModelVersion
from app.models.governance import ChangeRequest
from app.models.location import Connector, DemandObservation, IngestUpload, Location, MenuGroup
from app.models.location_access import LocationAccessLog
from app.models.pipeline import PipelineRun
from app.models.policy import PolicySimulation, PreparationPolicy
from app.models.quality import Alert, AlertRule, QualityIssue, QualityRule
from app.models.report import Report, ReportSchedule
from app.models.reservation import Reservation
from app.models.user import (
    MfaRecoveryCode,
    Session,
    User,
    UserIdentity,
    UserNotificationSetting,
    UserPreference,
    UserRole,
)
from app.models.waste import WasteObservation
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import Workspace

__all__ = [
    "Alert",
    "AlertRule",
    "ApiKey",
    "AuditLog",
    "Base",
    "BillingContact",
    "BillingInvoice",
    "BillingPaymentMethod",
    "BillingSubscription",
    "Calibration",
    "ChangeRequest",
    "Experiment",
    "ExperimentFold",
    "FeatureDefinition",
    "Connector",
    "ConnectorMapping",
    "DemandObservation",
    # Registered by the import above so their tables reach the metadata; listed
    # here too, or `ruff` reads the re-export as an unused import (F401).
    "Device",
    "DeviceEvent",
    "DeviceGrant",
    "Forecast",
    "ForecastExplanation",
    "IngestUpload",
    "Location",
    "LocationAccessLog",
    "MenuGroup",
    "MfaRecoveryCode",
    "MirrorSession",
    "ModelVersion",
    "PipelineRun",
    "PolicySimulation",
    "PreparationPolicy",
    "QualityIssue",
    "QualityRule",
    "Report",
    "ReportSchedule",
    "Reservation",
    "Session",
    "User",
    "UserIdentity",
    "UserNotificationSetting",
    "UserPreference",
    "UserRole",
    "WasteObservation",
    "WebhookDelivery",
    "WebhookEndpoint",
    "Workspace",
    "metadata",
]
