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
    dataFetchedAt: str | None = None
    collectedAt: str | None = None


class Evidence(K10Model):
    sourceRef: SourceReference
    claim: str
    relation: str | None = None
    uncertainty: str | None = None


class EvidenceDisclosureOut(K10Model):
    """Frozen truth boundary for a B39 assessment or published recommendation.

    This object is optional on its containing historical records.  Its absence
    means the older record did not carry this contract; it never means
    ``verified``.
    """
    verificationStatus: Literal["verified", "partially_supported", "unverified", "contradicted"]
    isRumor: bool
    originStatus: Literal["identified", "unknown"]
    originEvidenceRef: SourceReference | None = None
    unverifiedReasons: list[str] = Field(default_factory=list)
    conditionalAnalysis: str | None = None


class HistoricalCaseOut(K10Model):
    caseId: str
    outcome: Literal["success", "flat", "failure", "unclassified"]
    summary: str
    observedAt: str | None = None
    eventTime: str | None = None
    companyCode: str | None = None
    stage: str | None = None
    sourceRefs: list[SourceReference] = Field(default_factory=list)
    marketFacts: list[SourceReference] = Field(default_factory=list)
    outcomeFacts: dict[str, object] | None = None


class HistoricalCoverageOut(K10Model):
    state: Literal["complete", "partial", "unavailable"]
    requestedOutcomes: list[str] = Field(default_factory=list)
    presentOutcomes: list[str] = Field(default_factory=list)
    missingOutcomes: list[str] = Field(default_factory=list)
    reason: str | None = None
    sourceRefs: list[SourceReference] = Field(default_factory=list)


class OpportunityClassificationOut(K10Model):
    """Why a formal opportunity was created, as frozen with its comparison."""
    kind: Literal["initial", "independent", "material_stage"]
    reason: str = Field(min_length=1)
    newFacts: str = Field(min_length=1)
    changedJudgment: str | None = None
    twoDayReason: str = Field(min_length=1)
    relatedOpportunityId: str | None = None


class CandidateComparison(K10Model):
    summary: str | None = None
    rationale: str | None = None
    rank: int | None = None
    priorityReason: str | None = None
    gap: str | None = None
    rankChangeConditions: str | None = None
    twoDayReason: str | None = None
    classification: OpportunityClassificationOut | None = None
    historicalCases: list[HistoricalCaseOut] = Field(default_factory=list)
    historicalCoverage: HistoricalCoverageOut | None = None
    # `rank` is the frozen cross-event publication order.  Event-level rank is
    # deliberately separate so a reader never has to reverse-engineer it from
    # a later global ordering pass.
    eventRank: int | None = None
    rankNamespace: str | None = None
    evidenceDisclosure: EvidenceDisclosureOut | None = None


class ResearchAssessmentOut(K10Model):
    """One B39 comparison assessment, including non-publishable peers.

    The endpoint intentionally returns pending and excluded companies as well
    as primary/alternative/tied entries, so a reader can see that comparison
    coverage completed instead of mistaking a filtered recommendation list for
    the whole company set.
    """
    companyCode: str = Field(min_length=1)
    role: Literal["primary", "alternative", "tied", "pending", "excluded"]
    rank: int | None = Field(default=None, ge=1)
    summary: str = Field(min_length=1)
    priorityReason: str = Field(min_length=1)
    gap: str = Field(min_length=1)
    rankChangeConditions: str = Field(min_length=1)
    twoDayReason: str = Field(min_length=1)
    evidenceDisclosure: EvidenceDisclosureOut
    snapshotId: str = Field(min_length=1)
    snapshotRevision: int = Field(ge=1)
    eventId: str = Field(min_length=1)
    eventRevision: int = Field(ge=1)
    researchStatus: Literal["ready_for_comparison", "continue_research", "pending_verification", "abandon_recommendation", "background_only", "comparison_complete"]
    executionStatus: Literal["ok", "paused", "failed"]
    safeErrorCode: str | None = Field(default=None, max_length=120)


class ResearchAssessmentListOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    scanId: str
    items: list[ResearchAssessmentOut] = Field(default_factory=list)


class ResearchQuestionCountsOut(K10Model):
    open: int = Field(ge=0)
    answered: int = Field(ge=0)
    blocked: int = Field(ge=0)
    abandoned: int = Field(ge=0)


class ResearchCompanyCountsOut(K10Model):
    primary: int = Field(ge=0)
    alternative: int = Field(ge=0)
    tied: int = Field(ge=0)
    pending: int = Field(ge=0)
    excluded: int = Field(ge=0)
    comparable: int = Field(ge=0)


class ResearchSummaryOut(K10Model):
    """Safe scan-level B39 research state, calculated only by the store helper."""
    scanId: str = Field(min_length=1)
    taskId: str = Field(min_length=1)
    eventCount: int = Field(ge=0)
    questionCounts: ResearchQuestionCountsOut
    companyCounts: ResearchCompanyCountsOut
    researchStatusCounts: dict[str, int] = Field(default_factory=dict)
    executionStatusCounts: dict[str, int] = Field(default_factory=dict)
    safeFailureCounts: dict[str, int] = Field(default_factory=dict)
    comparisonComplete: bool
    executionFailed: bool


class CommonFactOut(K10Model):
    """A fact is always displayable; rawDetail preserves variable source shape."""
    key: str
    text: str
    rawDetail: dict[str, object] | None = None


class SourceReplayOut(K10Model):
    sourceKey: str | None = None
    nominalStartAt: str | None = None
    effectiveStartAt: str | None = None
    replayStartAt: str | None = None
    cutoffAt: str | None = None
    replaySeconds: int | None = Field(default=None, ge=1)
    requestState: str | None = None


class ExecutionDocumentCountsOut(K10Model):
    received: int = Field(default=0, ge=0)
    deduplicated: int = Field(default=0, ge=0)
    templateSkipped: int = Field(default=0, ge=0)
    understood: int = Field(default=0, ge=0)
    fullText: int = Field(default=0, ge=0)
    failedPending: int = Field(default=0, ge=0)


class ExecutionEventCountsOut(K10Model):
    verified: int = Field(default=0, ge=0)
    compared: int = Field(default=0, ge=0)
    # The count only exists after the immutable unified ranking freezes it.
    # A zero before that point would falsely claim the scan found no candidates.
    publishable: int | None = Field(default=None, ge=0)


class SafeExecutionFailureOut(K10Model):
    """Reader-safe failure data; raw provider output never reaches HTTP."""
    stage: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=120)
    # A frozen document-version reference, if one exists.  This permits a
    # reader to identify the affected item without exposing its body/hash.
    ref: str | None = Field(default=None, max_length=320)


class ExecutionRunControlOut(K10Model):
    """Reader-safe projection of the durable discovery switch.

    ``ready`` only means the switch is open.  It is deliberately separate from
    configuration and budget readiness, so an operator never mistakes a
    stopped service for an empty scan.
    """
    state: Literal["paused", "ready"]
    reasonCode: str = Field(min_length=1, max_length=120)
    changedAt: str


class ExecutionTitleCountsOut(K10Model):
    """Audited V3 title-triage result; absent on scans before this workflow."""
    received: int = Field(default=0, ge=0)
    exactDeduplicated: int = Field(default=0, ge=0)
    triaged: int = Field(default=0, ge=0)
    merged: int = Field(default=0, ge=0)
    notSelected: int = Field(default=0, ge=0)
    protected: int = Field(default=0, ge=0)
    partial: int = Field(default=0, ge=0)


class ExecutionArticleCountsOut(K10Model):
    """Selected article admission and deep-read outcomes, never a claim that
    every collected article body was read."""
    limit: int | None = Field(default=None, ge=0)
    selected: int = Field(default=0, ge=0)
    admitted: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    missingBody: int = Field(default=0, ge=0)
    tavilyExcerpt: int = Field(default=0, ge=0)
    tavilyFullArticle: int = Field(default=0, ge=0)


class ExecutionAttemptCountsOut(K10Model):
    """Safe call outcome counts. Token and monetary totals are intentionally
    kept in the internal audit ledger and never exposed as a UI quota."""
    started: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)


class ExecutionProgressOut(K10Model):
    state: Literal["running", "partial", "completed", "failed", "notConfigured", "paused", "retired"]
    stage: str | None = Field(default=None, max_length=80)
    documentCounts: ExecutionDocumentCountsOut = Field(default_factory=ExecutionDocumentCountsOut)
    eventCounts: ExecutionEventCountsOut = Field(default_factory=ExecutionEventCountsOut)
    coverageStatus: Literal["complete", "partial"]
    nextRetryAt: str | None = None
    safeFailures: list[SafeExecutionFailureOut] = Field(default_factory=list)
    strategyBinding: dict[str, object] | None = None
    executionBinding: dict[str, object] | None = None
    # Historical scans lack the V3 title ledger. Null is material: a client
    # must not claim a title pass or an article limit was measured for them.
    runControl: ExecutionRunControlOut | None = None
    titleCounts: ExecutionTitleCountsOut | None = None
    articleCounts: ExecutionArticleCountsOut | None = None
    attemptCounts: ExecutionAttemptCountsOut | None = None
    factCacheHits: int | None = Field(default=None, ge=0)
    researchSummary: ResearchSummaryOut | None = None


class SourceCoverageOut(K10Model):
    """A typed projection of a frozen source outcome.

    The producer may add operational diagnostics over time, so retain those
    additive fields while making time certainty and uncertain documents part
    of the public contract.
    """
    model_config = ConfigDict(extra="allow")

    sourceKey: str | None = None
    state: str | None = None
    complete: bool | None = None
    timeCoverage: Literal["complete", "partial"] | None = None
    # Older scans did not record this measurement.  Null keeps that absence
    # distinct from a measured zero.
    unknownPublicationTimeCount: int | None = Field(default=None, ge=0)
    uncertainTimeDocumentRefs: list[SourceReference] = Field(default_factory=list)


class ScanOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    scanId: str
    window: Literal["evening", "morning"]
    cutoffAt: str
    status: Literal["queued", "running", "completed", "partial", "failed", "not_configured"]
    coverageStatus: str
    coverageGaps: list[str] = Field(default_factory=list)
    sourceCoverage: list[SourceCoverageOut] = Field(default_factory=list)
    sourceReplay: SourceReplayOut | None = None
    publicationStatus: Literal["published", "not_published"]
    publicationBatchId: str | None = None
    availableAt: str | None = None
    configId: str | None = None
    configRevision: int | None = None
    createdAt: str
    completedAt: str | None = None
    # Historical scans predate item-level checkpoints; absence is not zero.
    executionProgress: ExecutionProgressOut | None = None
    # B36/B38 records predate proposition investigation.  Absence is material
    # and is never projected as a verified or completed comparison.
    researchSummary: ResearchSummaryOut | None = None


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
    independentVerificationRefs: list[SourceReference] = Field(default_factory=list)
    content: dict[str, object] = Field(default_factory=dict)
    occurredAt: str
    createdAt: str


class OpportunityOut(K10Model):
    strategyVersion: str | None = None
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
    displayRank: int | None = None
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
    canSelect: bool | None = None
    strategyVersion: str | None = None
    schemaVersion: str = SCHEMA_VERSION
    companyWindowId: str
    companyCode: str
    companyName: str | None = None
    firstBatchId: str
    displayRank: int | None = None
    availableAt: str | None = None
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
    chain: dict[str, object] | None = None
    historicalContext: dict[str, object] | None = None
    # Frozen disclosure included in the analysis prompt/context.  It remains
    # optional for historical analysis revisions.
    evidenceDisclosure: EvidenceDisclosureOut | None = None


class AnalysisSummaryOut(K10Model):
    commonFacts: list[str]
    disagreements: list[str]
    unknowns: list[str]


class AnalysisArtifactOut(K10Model):
    summary: AnalysisSummaryOut | None = None
    analysisId: str
    observationId: str
    revision: int
    role: Literal["pro", "con"]
    status: Literal["queued", "completed", "partial", "failed", "not_configured"]
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


class MarketFieldSourceOut(K10Model):
    source: str
    value: float | str | bool | None = None
    observedAt: str | None = None


class MarketFieldCheckOut(K10Model):
    field: str
    state: Literal["verified", "conflict", "single_source", "unavailable"]
    reason: str
    sourceValues: list[MarketFieldSourceOut] = Field(default_factory=list)


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
    fieldChecks: list[MarketFieldCheckOut] = Field(default_factory=list)
    anomalyReason: str | None = None


class CompanyWindowEvaluationOut(K10Model):
    companyWindowId: str
    opportunityIds: list[str] = Field(default_factory=list)
    companyCode: str
    sampleClass: Literal["primary", "overlap"]
    selection: SelectionSnapshotOut | None = None
    state: Literal["pending", "due", "completed", "incomplete", "not_configured"]
    evaluationConfigurationState: Literal["configured", "not_configured"] = "configured"
    evaluationConfigurationMissing: list[str] = Field(default_factory=list)
    evaluationConfigurationErrors: list[str] = Field(default_factory=list)
    revision: int
    updatedAt: str
    d1: MarketDayOut | None = None
    d2: MarketDayOut | None = None
    primaryEligible: bool
    closeLimitHitAny: bool | None = None
    consecutiveLimitUp: bool | None = None
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
    consecutiveLimitUpCount: int = Field(default=0, ge=0)
    incompleteCount: int = Field(default=0, ge=0)
    pendingCount: int = Field(default=0, ge=0)
    observedCompleteCount: int = Field(default=0, ge=0)
    knownHitCount: int = Field(default=0, ge=0)
    touchCount: int = Field(default=0, ge=0)
    suspendedCount: int = Field(default=0, ge=0)
    dataGapCount: int = Field(default=0, ge=0)
    anomalyCount: int = Field(default=0, ge=0)
    selectionPendingCount: int = Field(default=0, ge=0)
    notConfiguredCount: int = Field(default=0, ge=0)


class ResultsCohortOut(K10Model):
    batchId: str
    batchIds: list[str] = Field(default_factory=list)
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
    overlap: EvaluationMetricsOut = Field(default_factory=EvaluationMetricsOut)


class ResultsOut(K10Model):
    strategyVersion: str | None = None
    schemaVersion: str = SCHEMA_VERSION
    state: Literal["available", "not_configured"]
    reason: ApiFailure | None = None
    configurationState: Literal["configured", "not_configured"] = "configured"
    configurationMissing: list[str] = Field(default_factory=list)
    configurationErrors: list[str] = Field(default_factory=list)
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
    scope: Literal["candidate", "analysis", "evaluation", "discovery"]
    state: Literal["configured", "not_configured"]
    missing: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ConfigurationOut(K10Model):
    runControl: ExecutionRunControlOut | None = None
    strategyVersion: str | None = None
    strategySnapshotId: str | None = None
    universeSnapshotId: str | None = None
    profileSnapshotId: str | None = None
    universeSha256: str | None = None
    profilesSha256: str | None = None
    profileReviewStatus: str | None = None
    executionConfigId: str | None = None
    executionConfigRevision: int | None = None

    schemaVersion: str = SCHEMA_VERSION
    configId: str | None = None
    configRevision: int | None = None
    scopes: list[ConfigurationScopeOut]


class NotificationReadinessOut(K10Model):
    state: Literal["ready", "blocked", "notConfigured"]
    reasonCode: str | None = Field(default=None, max_length=120)
    nextRetryAt: str | None = None
    checkedAt: str


class OperationsReadinessOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    notificationReadiness: NotificationReadinessOut
    runControl: ExecutionRunControlOut


class DiscoveryPauseOut(K10Model):
    """Result of the one-way user pause operation.

    This API intentionally has no matching resume command: opening discovery
    remains an explicit operational action outside the client.
    """
    runControl: ExecutionRunControlOut


class MorningReportItemOut(K10Model):
    itemId: str
    reportId: str
    scanId: str
    companyWindowId: str | None = None
    opportunityId: str | None = None
    companyCode: str | None = None
    companyName: str | None = None
    displayRank: int | None = None
    selectionState: str | None = None
    lifecycle: str | None = None
    section: Literal["major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review"]
    priority: int
    summary: str
    coverage: dict[str, object]
    coverageStatus: str
    coverageGaps: list[str] = Field(default_factory=list)
    sourceRefs: list[SourceReference] = Field(default_factory=list)
    independentVerificationRefs: list[SourceReference] = Field(default_factory=list)
    # Absent on B36/B38 reports that predate frozen B39 disclosure.
    evidenceDisclosure: EvidenceDisclosureOut | None = None
    lifecycleEventId: str | None = None
    deadlineAt: str | None = None
    createdAt: str


class MorningReportOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    reportId: str
    scanId: str
    revision: int
    cutoffAt: str
    createdAt: str
    status: str
    coverage: dict[str, object]
    coverageStatus: str
    coverageGaps: list[str] = Field(default_factory=list)
    items: list[MorningReportItemOut] = Field(default_factory=list)


class MorningReportListOut(K10Model):
    items: list[MorningReportOut] = Field(default_factory=list)
    page: PageMeta = Field(default_factory=PageMeta)


class AnalysisDocumentRefIn(K10Model):
    documentId: str = Field(min_length=1, max_length=256)
    revision: int = Field(ge=1)


class AnalysisRequestIn(K10Model):
    kind: Literal["user_question", "evidence_update"]
    question: str | None = Field(default=None, max_length=6000)
    sourceRefs: list[AnalysisDocumentRefIn] = Field(default_factory=list, max_length=100)
    idempotencyKey: str = Field(min_length=1, max_length=256)


class AnalysisRequestOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    requestId: str
    companyWindowId: str
    observationId: str
    analysisJobId: str
    revision: int
    parentRevision: int
    inputCutoffAt: str
    replayed: bool


class AnalysisChainItemOut(K10Model):
    revision: int
    inputCutoffAt: str
    requestId: str | None = None
    kind: Literal["initial", "user_question", "evidence_update"]
    question: str | None = None
    parentRevision: int | None = None
    sourceRefs: list[SourceReference] = Field(default_factory=list)
    analyses: list[AnalysisArtifactOut] = Field(default_factory=list)
    job: JobOut | None = None


class AnalysisChainOut(K10Model):
    schemaVersion: str = SCHEMA_VERSION
    companyWindowId: str
    items: list[AnalysisChainItemOut] = Field(default_factory=list)


class V2CatalystOut(K10Model):
    lifecycleState: Literal["active", "risk", "withdrawn", "expired"] | None = None
    eventId: str
    eventRevision: int
    opportunityId: str | None = None
    companyWindowId: str
    headline: str
    summary: str
    classification: str
    verificationStatus: str


class CardPriceContextOut(K10Model):
    asOf: str
    collectedAt: str | None = None
    tradeDate: str
    pctChg: float
    sourceRefs: list[SourceReference]


class V2CardOut(K10Model):
    canSelect: bool | None = None
    sourceMarker: str | None = None
    latePublication: bool | None = None
    cardId: str
    companyCode: str
    companyName: str
    rank: int
    section: Literal["evening", "updated", "added"]
    companyWindowId: str
    currentSelectionState: Literal["kept", "skipped", "unhandled"]
    d1TradeDate: str
    d2TradeDate: str
    sampleClass: Literal["primary", "overlap"]
    strategyVersion: str
    summary: str
    twoDayReason: str
    uncertainty: list[str]
    sourceRefs: list[SourceReference]
    catalysts: list[V2CatalystOut]
    priceReaction: str | None = None
    priceContext: CardPriceContextOut | None = None


class V2LifecycleUpdateOut(K10Model):
    updateId: str
    opportunityId: str
    companyWindowId: str
    companyCode: str
    companyName: str
    kind: Literal['risk', 'withdrawal', 'expiry', 'evidence_update']
    reason: str
    createdAt: str
    sourceRefs: list[SourceReference]


class V2IncompleteReviewOut(K10Model):
    taskId: str
    opportunityId: str
    companyWindowId: str
    companyCode: str
    status: str
    reason: str


class V2ReportOut(K10Model):
    coverageGaps: list[str] = Field(default_factory=list)
    incompleteReviews: list[V2IncompleteReviewOut] = Field(default_factory=list)
    lifecycleUpdates: list[V2LifecycleUpdateOut] = Field(default_factory=list)
    reportId: str
    strategyVersion: str
    strategySnapshotId: str
    windowKind: Literal["evening", "morning"]
    parentReportId: str | None = None
    cutoffAt: str
    verificationCutoffAt: str | None = None
    availableAt: str | None = None
    status: str
    eveningCards: list[V2CardOut]
    updatedCards: list[V2CardOut]
    addedCards: list[V2CardOut]
    nextCursor: str | None = None


class V2ReportEnvelope(K10Model):
    schemaVersion: int = 8
    state: Literal["available", "empty", "not_configured"]
    reason: ApiFailure | None = None
    report: V2ReportOut | None = None


class V2ReportSummaryOut(K10Model):
    reportId: str
    strategyVersion: str
    windowKind: Literal['evening', 'morning']
    cutoffAt: str
    availableAt: str | None = None
    status: str


class V2ReportListOut(K10Model):
    items: list[V2ReportSummaryOut]
    page: PageMeta
