export type Verbosity = "concise" | "standard" | "deep" | "custom";
export type EnhancementState = "idle" | "streaming" | "needs_clarification" | "ready" | "incomplete" | "error";

export type ChangeType = "clarify" | "add_context" | "add_constraint" | "format" | "safety" | "remove_redundancy";

export interface PromptChange {
  id: string;
  type: ChangeType;
  before: string;
  after: string;
  reason: string;
  state: "pending" | "accepted" | "rejected";
}

export interface Suggestion {
  id: string;
  kind: "goal" | "context" | "format" | "constraint" | "alternate_intent";
  title: string;
  purpose: string;
  content: string;
  operation: "insert" | "replace";
  anchor: string;
  applied: boolean;
}

export interface Assumption {
  id: string;
  text: string;
  confirmed: boolean;
}

export interface ClarifyingQuestion {
  id: string;
  text: string;
  why_needed: string;
}

export interface RiskFlag {
  category: "destructive" | "medical" | "legal" | "financial" | "credential" | "privacy" | "factual";
  message: string;
  required_protection: string;
}

export interface EnhancementResult {
  status: "ready" | "needs_clarification";
  task_type?: string;
  primary_prompt: string;
  assumptions: Assumption[];
  questions: ClarifyingQuestion[];
  changes: PromptChange[];
  suggestions: Suggestion[];
  risk_flags: RiskFlag[];
}

export interface Attachment {
  id: string;
  name: string;
  path?: string;
  kind: string;
  chars: number;
  extractedText: string;
  sourceDeleted: boolean;
}

export interface ProviderConfig {
  baseUrl: string;
  hasApiKey: boolean;
  defaultModel: string;
  v4FlashModelId: string;
  inputPrice: number;
  outputPrice: number;
  models: string[];
}

export interface UsageRecord {
  inputTokens: number;
  outputTokens: number;
  estimatedCost: number;
  monthTotal: number;
}

export interface ProfileRule {
  id: string;
  preferenceType: string;
  label: string;
  value: string;
  confidence: number;
  explicit: boolean;
}

export interface HistoryRecord {
  id: string;
  title: string;
  original: string;
  enhanced: string;
  createdAt: string;
  model: string;
  target: string;
}

export interface LocalSettings {
  clearClipboard: boolean;
  profileEnabled: boolean;
  customTargetUrl: string;
  monthlyWarningLimit: number;
  monthlyLimit: number;
  profileRules: ProfileRule[];
}

export interface BackendEvent {
  type: "delta" | "status" | "result" | "usage" | "error";
  data?: string;
  result?: EnhancementResult;
  usage?: UsageRecord;
  code?: string;
  message?: string;
}

export interface EnhancementRequest {
  originalText: string;
  contextText: string;
  attachments: Array<{ name: string; text: string }>;
  model: string;
  targetModel: string;
  verbosity: Verbosity;
  customInstructions?: string;
  clarificationRound: number;
  clarificationAnswers: Array<{ questionId: string; answer: string }>;
  profileSummary: string[];
}
