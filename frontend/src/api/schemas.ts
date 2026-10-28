/**
 * Runtime schemas for every backend response the UI uses (API.md).
 * Responses are validated; unknown fields are stripped. A shape mismatch
 * raises an `invalid_response` error instead of rendering wrong data.
 */
import * as z from "zod";

export const Verdict = z.enum(["CONFIRMED", "NOT_REPRODUCED", "INCONCLUSIVE"]);
export type Verdict = z.infer<typeof Verdict>;
export const Severity = z.enum(["critical", "high", "medium", "low"]);
export type Severity = z.infer<typeof Severity>;
export const CheckOutcome = z.enum(["PASS", "FAIL", "ERROR"]);

const nstr = z.string().nullable();

export const Totals = z.object({
  selected: z.number(),
  executed: z.number(),
  CONFIRMED: z.number(),
  NOT_REPRODUCED: z.number(),
  INCONCLUSIVE: z.number(),
  not_run: z.number().default(0),
  pending: z.number(),
});
export type Totals = z.infer<typeof Totals>;

export const TargetSnapshot = z.object({
  image: z.string(),
  mode: z.string(),
  config_version: z.number().optional(),
  name: z.string().optional(),
});

export const Run = z.object({
  id: z.string(),
  status: z.string(),
  status_reason: nstr,
  kind: z.enum(["standard", "retest"]),
  target_id: z.string(),
  contract_id: z.string(),
  target_config_snapshot: TargetSnapshot,
  scenario_count: z.number(),
  cancel_requested: z.boolean(),
  created_at: z.string(),
  started_at: nstr,
  finished_at: nstr,
  totals: Totals,
  jury: z.string(),
});
export type Run = z.infer<typeof Run>;
export const RunList = z.object({ items: z.array(Run), next_cursor: nstr });

export const ExecutionSummary = z.object({
  id: z.string(),
  scenario_key: z.string(),
  category: z.string(),
  status: z.string(),
  verdict: Verdict.nullable(),
  inconclusive_reason: nstr,
  severity: Severity.nullable(),
  task_completed: z.boolean().nullable(),
  duration_ms: z.number().nullable(),
});
export type ExecutionSummary = z.infer<typeof ExecutionSummary>;
export const ExecutionList = z.object({ items: z.array(ExecutionSummary), next_cursor: nstr });

export const ToolCall = z.object({
  id: z.string(),
  seq: z.number(),
  tool: z.string(),
  args: z.record(z.string(), z.unknown()).nullable(),
  outcome: z.string(),
  side_effect: z.boolean(),
});
export type ToolCall = z.infer<typeof ToolCall>;

export const Check = z.object({
  check_id: z.string(),
  outcome: CheckOutcome,
  provenance: z.string(),
  rule_ref: z.string(),
  evidence_refs: z.array(z.string()),
  message: z.string(),
});
export type Check = z.infer<typeof Check>;

export const DiffRow = z.object({
  table: z.string(),
  key: z.union([z.string(), z.number()]),
  change: z.enum(["+", "-", "~"]),
  before: z.record(z.string(), z.unknown()).nullable(),
  after: z.record(z.string(), z.unknown()).nullable(),
});
export type DiffRow = z.infer<typeof DiffRow>;

export const JuryReview = z.object({
  role: z.string(),
  provenance: z.string(),
  status: z.string(),
  model: z.string(),
  prompt_version: z.string(),
  output: z.record(z.string(), z.unknown()).nullable(),
});
export type JuryReview = z.infer<typeof JuryReview>;

export const ArtifactRef = z.object({
  id: z.string(),
  kind: z.string(),
  sha256: z.string(),
  truncated: z.boolean(),
});

export const ExecutionDetail = z.object({
  execution_id: z.string(),
  scenario: z.object({ key: z.string(), version: z.number(), category: z.string(), title: nstr.optional() }),
  status: z.string(),
  verdict: Verdict.nullable(),
  inconclusive_reason: nstr,
  severity: Severity.nullable(),
  task_completed: z.boolean().nullable(),
  started_at: nstr,
  finished_at: nstr,
  agent_claim: z.object({ artifact_id: z.string(), provenance: z.string(), text: z.string() }).nullable(),
  verified_outcome: z.object({ provenance: z.string(), tool_calls: z.array(ToolCall) }),
  checks: z.array(Check),
  state_diff: z.array(DiffRow).nullable(),
  evidence_problems: z.array(z.string()),
  jury: z.array(JuryReview),
  artifacts: z.array(ArtifactRef),
  sandbox_meta: z.object({
    duration_ms: z.number().nullable().optional(),
    agent_exit_code: z.number().nullable().optional(),
    cleanup_ok: z.boolean().nullable().optional(),
    outcome: nstr.optional(),
    agent_image: nstr.optional(),
    agent_image_id: nstr.optional(),
  }),
});
export type ExecutionDetail = z.infer<typeof ExecutionDetail>;

export const Artifact = z.object({
  id: z.string(),
  kind: z.string(),
  content_redacted: z.string(),
  sha256: z.string(),
  truncated: z.boolean(),
});
export type Artifact = z.infer<typeof Artifact>;

export const Target = z.object({
  id: z.string(),
  name: z.string(),
  adapter_type: z.string(),
  config: z.object({ image: z.string(), mode: z.string() }),
  config_version: z.number(),
  is_local: z.boolean(),
  authorization_status: z.string(),
  authorized_by: z.string(),
  authorized_at: z.string(),
});
export type Target = z.infer<typeof Target>;
export const TargetList = z.object({ items: z.array(Target) });

export const ContractBody = z.object({
  name: z.string(),
  version: z.number(),
  task: z.string(),
  principal: z.string(),
  allowed_tools: z.array(z.string()),
  forbidden_tools: z.array(z.string()),
  argument_constraints: z.array(z.record(z.string(), z.unknown())),
  invariants: z.array(z.record(z.string(), z.unknown())),
  data_rules: z.array(z.record(z.string(), z.unknown())),
  reliability: z.array(z.record(z.string(), z.unknown())),
});
export type ContractBody = z.infer<typeof ContractBody>;
export const ContractSummary = z.object({
  id: z.string(),
  name: z.string(),
  version: z.number(),
  body_sha256: z.string(),
});
export const ContractValidation = z.object({ valid: z.boolean(), warnings: z.array(z.string()) });
export const ContractList = z.object({ items: z.array(ContractSummary) });
export const ContractDetail = ContractSummary.extend({ body: ContractBody });
export type ContractDetail = z.infer<typeof ContractDetail>;

export const ScenarioBody = z.object({
  key: z.string(),
  title: z.string(),
  category: z.string(),
  description: z.string(),
  principal: z.string(),
  user_message: z.string(),
  seed: z.object({
    order_notes: z.record(z.string(), z.string()).default({}),
    faults: z.array(z.record(z.string(), z.unknown())).default([]),
    approvals: z.array(z.record(z.string(), z.unknown())).default([]),
  }),
  targets: z.array(z.string()),
  expected_safe_behaviour: z.string(),
  source: z.string().optional(),
});
export type ScenarioBody = z.infer<typeof ScenarioBody>;
export const Scenario = z.object({
  id: z.string(),
  key: z.string(),
  version: z.number(),
  category: z.string(),
  source: z.string(),
  title: nstr,
  body_sha256: z.string(),
  body: ScenarioBody.optional(),
});
export type Scenario = z.infer<typeof Scenario>;
export const ScenarioList = z.object({ items: z.array(Scenario) });

export const FindingListItem = z.object({
  id: z.string(),
  run_id: z.string(),
  execution_id: z.string(),
  title: z.string(),
  category: z.string(),
  verdict: z.enum(["CONFIRMED", "INCONCLUSIVE"]),
  severity: Severity.nullable(),
  failed_checks: z.array(z.string()),
  scenario_key: z.string(),
  target: nstr,
  target_mode: nstr,
  regression_test_id: nstr,
  created_at: z.string(),
});
export type FindingListItem = z.infer<typeof FindingListItem>;
export const FindingList = z.object({ items: z.array(FindingListItem) });

export const FindingDetail = z.object({
  id: z.string(),
  run_id: z.string(),
  execution_id: z.string(),
  title: z.string(),
  category: z.string(),
  verdict: z.enum(["CONFIRMED", "INCONCLUSIVE"]),
  severity: Severity.nullable(),
  failed_checks: z.array(z.string()),
  reproduction_steps: z.array(z.string()),
});
export type FindingDetail = z.infer<typeof FindingDetail>;

export const Retest = z.object({
  run_id: z.string(),
  status: z.string(),
  verdict: Verdict.nullable(),
  target_mode: nstr,
  config_version: z.number().nullable().optional(),
  finished_at: nstr,
});
export type Retest = z.infer<typeof Retest>;
export const RegressionTest = z.object({
  id: z.string(),
  finding_id: z.string(),
  title: nstr,
  scenario_key: nstr,
  original_verdict: nstr,
  original_severity: nstr,
  original_run_id: nstr,
  expected_checks: z.record(z.string(), z.string()),
  body_sha256: z.string(),
  created_at: z.string(),
  retests: z.array(Retest),
});
export type RegressionTest = z.infer<typeof RegressionTest>;
export const RegressionList = z.object({ items: z.array(RegressionTest) });
export const RegressionExport = z.object({ id: z.string(), finding_id: z.string(), body_sha256: z.string() });

export const AuditEvent = z.object({
  id: z.string(),
  action: z.string(),
  actor: z.string(),
  entity_type: nstr,
  entity_id: nstr,
  details: z.record(z.string(), z.unknown()),
  created_at: z.string(),
});
export type AuditEvent = z.infer<typeof AuditEvent>;
export const AuditList = z.object({ items: z.array(AuditEvent) });

export const Ready = z.object({
  status: z.enum(["ready", "not_ready"]),
  checks: z.record(z.string(), z.string()),
});
export type Ready = z.infer<typeof Ready>;

export const Settings = z.object({
  app: z.object({ name: z.string(), version: z.string(), env: z.string() }),
  sandbox: z.object({
    adapter_types: z.array(z.string()),
    gateway_image: z.string(),
    allowed_target_images: z.array(z.string()),
    network: z.string(),
    filesystem: z.string(),
    user: z.string(),
    mem_bytes: z.number(),
    cpus: z.number(),
    pids_limit: z.number(),
    exec_timeout_s: z.number(),
    run_timeout_s: z.number(),
  }),
  jury: z.object({
    enabled: z.boolean(),
    provider: z.string(),
    model: nstr,
    credentials_configured: z.boolean().nullable(),
  }),
  limits: z.object({
    rate_limits_per_minute: z.record(z.string(), z.number()),
    max_scenarios_per_run: z.number(),
  }),
  retention: z.object({ implemented: z.boolean(), note: z.string() }),
});
export type Settings = z.infer<typeof Settings>;

export const ErrorBody = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.record(z.string(), z.unknown()).optional(),
    request_id: nstr.optional(),
  }),
});
