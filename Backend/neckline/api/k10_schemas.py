"""K10-v1.4 HTTP wire contract.

K10 is a published-stock selector.  The public model is a company opportunity
and its fixed two-trading-day window; it intentionally has no trade-plan or
price-plan vocabulary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "k10-api-v2"


class K10Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiFailure(K10Model):
    reason: str
    message: str
    missing: list[str] = Field(default_factory=list)


class PageMeta(K10Model):
    nextCursor: str | None = None


class SourceReference(K10Model):
    documentId: str | None = None
    factId: str | None = None
    companyCode: str | None = None
    tradeDate: str | None = None
    revision: int | None = None
    sourceKey: str | None = None
    title: str | None = None
    url: str | None = None
    excerpt: str | None = None
    publishedAt: str | None = None
    publishedPrecision: Literal["exact", "date", "unknown"] = "unknown"
    fetchedAt: str | None = None
    collectedAt: str | None = None


class Evidence(K10Model):
    sourceRef: SourceReference
    claim: str
    relation: str | None = None
    uncertainty: str | None = None


class CandidateComparison(K10Model):
    summary: str | None = None
    rationale: str | None = None
    rank: int | None = None
    priorityReason: str | None = None
    gap: str | None = None
    rankChangeConditions: str | None = None
    twoDayReason: str | None = None


class CommonFactOut(K10Model):
    """A fact is always displayable; rawDetail preserves variable source shape."""
    key: str
    text: str
    rawDetail: dict[str, object] | None = None


class ScanOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    scanId: str
    window: Literal["evening", "morning"]
    cutoffAt: str
    status: Literal["queued", "running", "completed", "partial", "failed", "not_configured"]
    coverageStatus: str
    coverageGaps: list[str] = Field(default_factory=list)
    sourceCoverage: list[dict[str, object]] = Field(default_factory=list)
    publicationStatus: Literal["published", "not_published"]
    publicationBatchId: str | None = None
    availableAt: str | None = None
    configId: str | None = None
    configRevision: int | None = None
    createdAt: str
    completedAt: str | None = None


class PublicationOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    batchId: str
    scanId: str
    publicationKind: Literal["evening", "morning"]
    availableAt: str
    createdAt: str
    sampleCount: int = Field(ge=0)


class PublicationListOut(K10Model):
    items: list[PublicationOut] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class LifecycleEventOut(K10Model):
    lifecycleEventId: str
    kind: Literal["published", "evidence_update", "risk", "withdrawal", "expired"]
    reason: str | None = None
    sourceRefs: list[SourceReference] = Field(default_factory=list)
    content: dict[str, object] = Field(default_factory=dict)
    occurredAt: str
    createdAt: str


class OpportunityOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    opportunityId: str
    opportunityKey: str
    companyCode: str
    companyName: str | None = None
    eventId: str
    eventRevision: int
    catalystStage: str
    relatedOpportunityId: str | None = None
    companyWindowId: str
    firstBatchId: str
    availableAt: str
    sourceMarker: Literal["evening", "morning"] | None = None
    latePublication: bool | None = None
    d0TradeDate: str
    d1TradeDate: str
    d2TradeDate: str
    sampleClass: Literal["primary", "overlap"]
    overlapsWindowId: str | None = None
    lifecycle: Literal["published", "evidence_update", "risk", "withdrawal", "expired"]
    createdAt: str


class PublicationSampleOut(K10Model):
    sampleId: str
    batchId: str
    companyWindowId: str
    opportunityId: str
    companyCandidateId: str
    companyCode: str
    companyName: str | None = None
    eventId: str
    eventRevision: int
    category: str
    sourceMarker: str
    comparison: CandidateComparison
    evidence: list[Evidence] = Field(default_factory=list)
    rank: int | None = None
    createdAt: str


class OpportunityDetail(OpportunityOut):
    eventHeadline: str | None = None
    commonFacts: list[CommonFactOut] = Field(default_factory=list)
    samples: list[PublicationSampleOut] = Field(default_factory=list)
    lifecycleEvents: list[LifecycleEventOut] = Field(default_factory=list)


class OpportunityListOut(K10Model):
    items: list[OpportunityOut] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class SelectionSnapshotOut(K10Model):
    state: Literal["selected", "skipped", "unhandled"]
    frozenAt: str
    actionIds: list[str] = Field(default_factory=list)


class CompanyWindowOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    companyWindowId: str
    companyCode: str
    companyName: str | None = None
    firstBatchId: str
    d0TradeDate: str
    d1TradeDate: str
    d2TradeDate: str
    d1SelectionAt: str
    d2CloseAt: str
    sampleClass: Literal["primary", "overlap"]
    overlapsWindowId: str | None = None
    selection: SelectionSnapshotOut | None = None
    currentSelectionState: Literal["kept", "skipped", "unhandled"] = "unhandled"
    lastActionAt: str | None = None
    postFreeze: bool = False
    opportunities: list[OpportunityOut] = Field(default_factory=list)
    samples: list[PublicationSampleOut] = Field(default_factory=list)
    createdAt: str


class CompanyWindowListOut(K10Model):
    items: list[CompanyWindowOut] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class SelectionActionIn(K10Model):
    action: Literal["keep", "skip", "restore", "withdraw"]
    idempotencyKey: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=2000)


class SelectionActionOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    actionId: str
    companyWindowId: str
    representativeCandidateId: str | None = None
    state: Literal["kept", "skipped", "unhandled"]
    lastActionAt: str | None = None
    postFreeze: bool = False
    observationId: str | None = None
    analysisJobId: str | None = None
    replayed: bool = False


class AnalysisEventLineage(K10Model):
    eventId: str
    revision: int


class ModelCost(K10Model):
    amount: float | None = None
    currency: str | None = None
    pricingVersion: str | None = None
    status: str | None = None


class ModelUsage(K10Model):
    inputTokens: int | None = None
    outputTokens: int | None = None
    totalTokens: int | None = None
    cacheTokens: int | None = None
    usageUnavailable: bool | None = None
    cacheUsageUnavailable: bool | None = None
    cost: ModelCost | None = None


class AnalysisInputLineage(K10Model):
    candidateId: str | None = None
    event: AnalysisEventLineage | None = None
    mappingIds: list[str] = Field(default_factory=list)
    documentVersions: list[SourceReference] = Field(default_factory=list)
    inputCutoffAt: str | None = None
    marketContext: dict[str, object] | None = None
    proAnalysis: dict[str, object] | None = None


class AnalysisArtifactOut(K10Model):
    analysisId: str
    observationId: str
    revision: int
    role: Literal["pro", "con"]
    status: Literal["queued", "completed", "failed", "not_configured"]
    inputCutoffAt: str
    sourceRefs: list[SourceReference] = Field(default_factory=list)
    inputLineage: AnalysisInputLineage
    fullText: str | None = None
    provider: str | None = None
    model: str | None = None
    promptVersion: str | None = None
    usage: ModelUsage | None = None
    error: str | None = None


class JobOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    jobId: str
    kind: str
    status: Literal["queued", "running", "completed", "failed", "not_configured", "cancelled"]
    stage: str
    attemptCount: int
    inputVersion: str
    inputCutoffAt: str
    createdAt: str
    updatedAt: str
    error: ApiFailure | None = None


class SelectionDetailOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    companyWindowId: str
    representativeCandidateId: str | None = None
    state: Literal["kept", "skipped", "unhandled"]
    lastActionAt: str | None = None
    postFreeze: bool = False
    observationId: str | None = None
    opportunities: list[OpportunityOut] = Field(default_factory=list)
    analyses: list[AnalysisArtifactOut] = Field(default_factory=list)
    analysisJobId: str | None = None
    latestJob: JobOut | None = None


class SelectionListOut(K10Model):
    items: list[SelectionDetailOut] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class JobRetryIn(K10Model):
    expectedAttemptCount: int = Field(ge=0)


class MarketDayOut(K10Model):
    tradeDate: str
    availability: Literal["available", "suspended", "data_gap", "anomaly"]
    closeLimitUp: bool | None = None
    touchedLimitUp: bool | None = None
    firstTouchedAt: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    preClose: float | None = None
    limitUpPrice: float | None = None
    sourceRefs: list[SourceReference] = Field(default_factory=list)
    obtainedAt: str | None = None


class CompanyWindowEvaluationOut(K10Model):
    companyWindowId: str
    opportunityIds: list[str] = Field(default_factory=list)
    companyCode: str
    sampleClass: Literal["primary", "overlap"]
    selection: SelectionSnapshotOut | None = None
    state: Literal["pending", "due", "completed", "incomplete"]
    revision: int
    updatedAt: str
    d1: MarketDayOut | None = None
    d2: MarketDayOut | None = None
    primaryEligible: bool
    closeLimitHitAny: bool | None = None
    firstTouchDay: Literal["D1", "D2"] | None = None
    firstTouchStatus: str | None = None
    knownTouchDays: list[Literal["D1", "D2"]] = Field(default_factory=list)
    d1OpenGap: float | None = None
    d1PriceChanges: dict[str, float | None] = Field(default_factory=dict)
    d2PriceChanges: dict[str, float | None] = Field(default_factory=dict)
    windowPriceChanges: dict[str, float | None] = Field(default_factory=dict)
    comparability: str | None = None
    gaps: list[str] = Field(default_factory=list)
    factRefs: list[SourceReference] = Field(default_factory=list)


class EvaluationMetricsOut(K10Model):
    sampleCount: int = Field(default=0, ge=0)
    eligibleCount: int = Field(default=0, ge=0)
    hitCount: int = Field(default=0, ge=0)
    hitRate: float | None = None
    touchRate: float | None = None
    incompleteCount: int = Field(default=0, ge=0)
    pendingCount: int = Field(default=0, ge=0)
    observedCompleteCount: int = Field(default=0, ge=0)
    knownHitCount: int = Field(default=0, ge=0)
    touchCount: int = Field(default=0, ge=0)
    suspendedCount: int = Field(default=0, ge=0)
    dataGapCount: int = Field(default=0, ge=0)
    anomalyCount: int = Field(default=0, ge=0)
    selectionPendingCount: int = Field(default=0, ge=0)


class ResultsCohortOut(K10Model):
    batchId: str
    d1TradeDate: str
    d2TradeDate: str
    evaluationVersion: str | None = None
    companySampleCount: int = Field(ge=0)
    catalystEventCount: int = Field(ge=0)
    primary: dict[str, EvaluationMetricsOut] = Field(default_factory=dict)
    overlap: EvaluationMetricsOut = Field(default_factory=EvaluationMetricsOut)


class ResultsEventGroupOut(K10Model):
    eventId: str
    headline: str | None = None
    companyWindowIds: list[str] = Field(default_factory=list)
    opportunityIds: list[str] = Field(default_factory=list)
    companySampleCount: int = Field(ge=0)
    catalystCount: int = Field(ge=0)
    primary: dict[str, EvaluationMetricsOut] = Field(default_factory=dict)


class ResultsOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    state: Literal["available", "not_configured"]
    reason: ApiFailure | None = None
    asOf: str | None = None
    primary: dict[str, EvaluationMetricsOut] = Field(default_factory=dict)
    overlap: EvaluationMetricsOut = Field(default_factory=EvaluationMetricsOut)
    records: list[CompanyWindowEvaluationOut] = Field(default_factory=list)
    cohorts: list[ResultsCohortOut] = Field(default_factory=list)
    eventGroups: list[ResultsEventGroupOut] = Field(default_factory=list)


class SourceDocumentPageOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    documentId: str
    revision: int
    sourceKey: str
    externalId: str
    canonicalUrl: str | None = None
    title: str | None = None
    publishedAt: str | None = None
    publishedPrecision: Literal["exact", "date", "unknown"]
    fetchedAt: str
    excerpt: str | None = None
    body: str | None = None
    page: PageMeta = Field(default_factory=PageMeta)


class ConfigurationScopeOut(K10Model):
    scope: Literal["candidate", "analysis", "evaluation"]
    state: Literal["configured", "not_configured"]
    missing: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ConfigurationOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    configId: str | None = None
    configRevision: int | None = None
    scopes: list[ConfigurationScopeOut]
