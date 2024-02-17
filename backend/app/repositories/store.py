"""Factory for repositories sharing one table and cursor codec."""

from app.core.pagination import CursorCodec
from app.repositories.audit import AuditRepository
from app.repositories.billing import BillingRepository
from app.repositories.documents import DocumentRepository
from app.repositories.dynamo import DynamoTable
from app.repositories.leases import LeaseRepository
from app.repositories.maintenance import MaintenanceRepository
from app.repositories.members import MemberRepository
from app.repositories.notifications import NotificationRepository
from app.repositories.organisations import OrganisationRepository
from app.repositories.platform import PlatformRepository
from app.repositories.privacy import PrivacyRepository
from app.repositories.properties import PropertyRepository, UnitRepository
from app.repositories.tenants import TenantRepository


class Store:
    def __init__(self, table: DynamoTable, cursors: CursorCodec) -> None:
        self.table, self.cursors = table, cursors

    def org(self, org_id: str) -> OrganisationRepository:
        return OrganisationRepository(self.table, self.cursors, org_id)

    def members(self, org_id: str) -> MemberRepository:
        return MemberRepository(self.table, self.cursors, org_id)

    def properties(self, org_id: str) -> PropertyRepository:
        return PropertyRepository(self.table, self.cursors, org_id)

    def units(self, org_id: str) -> UnitRepository:
        return UnitRepository(self.table, self.cursors, org_id)

    def audit(self, org_id: str) -> AuditRepository:
        return AuditRepository(self.table, self.cursors, org_id)

    def platform(self) -> PlatformRepository:
        return PlatformRepository(self.table, self.cursors)

    def tenants(self, org_id: str) -> TenantRepository:
        return TenantRepository(self.table, self.cursors, org_id)

    def leases(self, org_id: str) -> LeaseRepository:
        return LeaseRepository(self.table, self.cursors, org_id)

    def documents(self, org_id: str) -> DocumentRepository:
        return DocumentRepository(self.table, self.cursors, org_id)

    def privacy(self, org_id: str) -> PrivacyRepository:
        return PrivacyRepository(self.table, self.cursors, org_id)

    def billing(self, org_id: str) -> BillingRepository:
        return BillingRepository(self.table, self.cursors, org_id)

    def maintenance(self, org_id: str) -> MaintenanceRepository:
        return MaintenanceRepository(self.table, self.cursors, org_id)

    def notifications(self, org_id: str) -> NotificationRepository:
        return NotificationRepository(self.table, self.cursors, org_id)
