# Groq provider review snapshot — 2026-09-18

Status: engineering/provider-fact review for the closed TurBot AI beta. This is **not** a legal opinion and does not approve public free-form AI chat.

The purpose of this snapshot is to separate facts published by Groq from deployment-specific items that still require human verification.

## Official sources checked

- Groq Your Data in GroqCloud: https://console.groq.com/docs/your-data
- Groq Services Agreement: https://console.groq.com/docs/legal/services-agreement
- Groq Customer Data Processing Addendum: https://console.groq.com/docs/legal/customer-data-processing-addendum
- Groq Acceptable Use & Responsible AI Policy: https://console.groq.com/docs/legal/ai-policy
- Groq model deprecations: https://console.groq.com/docs/deprecations
- Groq supported models: https://console.groq.com/docs/models
- Groq subprocessor list referenced by the DPA: https://trust.groq.com/subprocessors

Re-check these sources before a material provider/model change or public release.

## Provider facts found on 2026-09-18

### Inference retention and ZDR

Groq states that inference requests are not retained by default as application state. Inputs and outputs may nevertheless be temporarily logged for platform reliability troubleshooting or suspected-abuse investigation, with those logs retained for up to 30 days unless longer retention is legally required.

Groq states that all customers can enable Zero Data Retention (ZDR) in Data Controls. When ZDR is enabled, customer data is not retained for reliability/abuse monitoring and retention-dependent features are disabled.

**Engineering consequence:** free-form external beta calls fail closed unless the deployment operator explicitly enables the external-provider gate and confirms that ZDR is enabled in the actual Groq organization.

The repository cannot verify a Console setting. `GROQ_ZDR_CONFIRMED=true` is therefore a manual deployment assertion, not a technical proof.

### Data location

Groq states that retained customer data is stored in Google Cloud Platform buckets in the United States.

**Engineering consequence:** this is a cross-border data-flow fact that must be evaluated for the actual operator and user population before public arbitrary-text use. Local redaction helps minimize data but does not make arbitrary text reliably anonymous.

### Model training

Groq's current Services Agreement states that Inputs/Outputs are not permitted to be used for training or fine-tuning models unless the customer explicitly grants permission or instructs Groq to do so.

**Engineering consequence:** do not opt into separate training/fine-tuning or persistence features for this beta without a new review.

### Processor / DPA structure

Groq's DPA states that, depending on the relationship, the customer is a Controller or Processor and Groq acts as Processor or sub-Processor for covered Cloud Services processing. The DPA references a maintained subprocessor list and describes security/incident obligations, including notification within 72 hours after Groq becomes aware of a covered Data Breach.

**Engineering consequence:** the business/operator must determine whether the published DPA governs the actual account/relationship and retain the applicable contractual record. The code cannot establish that fact.

### Model lifecycle

Groq's deprecation page states that `llama-3.3-70b-versatile` was shut down for Free/Developer usage on 2026-08-16. Groq recommends `openai/gpt-oss-120b` or `qwen/qwen3.6-27b` as replacements.

**Engineering consequence:** TurBot now defaults to `openai/gpt-oss-120b`. A regression test prevents the deprecated model from returning as a default.

## Closed-beta acceptance matrix

| Control | Status | Evidence / action |
| --- | --- | --- |
| Public free-form AI default OFF | Implemented | `AI_CHAT_ENABLED=false` |
| Tester allowlist | Implemented | `ADMIN_ID` + `AI_CHAT_BETA_IDS` |
| External provider requires separate opt-in | Implemented | `AI_CHAT_EXTERNAL_PROVIDER_ENABLED=false` |
| ZDR must be manually confirmed before external beta call | Implemented gate, deployment verification pending | Enable ZDR in Groq Console, then set `GROQ_ZDR_CONFIRMED=true` |
| Deprecated Groq default removed | Implemented | Default `openai/gpt-oss-120b` |
| Sensitive input blocked/redacted | Implemented + automated tests | deterministic guardrails |
| Restricted legal/visa/health/insurance routing | Implemented + automated tests | manager/verified-source handoff |
| Unverified model price/availability/legal/visa claims blocked | Implemented + automated tests | post-generation claim guard |
| Provider outage/empty response fail closed | Implemented + automated tests | deterministic fallback |
| AI telemetry excludes prompt/response/chat ID | Implemented + automated tests | aggregate counters only |
| Actual Groq organization has ZDR enabled | **Pending human verification** | Groq Console Data Controls |
| Actual account is covered by appropriate agreement/DPA | **Pending human/business verification** | account/contract records |
| Current subprocessor list reviewed for deployment | **Pending human review** | Groq Trust Center |
| Russian localization/cross-border analysis for actual operator | **Pending legal review** | 152-FZ / actual architecture |
| Operator privacy/consent copy reflects external AI flow | **Pending legal/business review** | production privacy documents |
| #90 P0 release gates green | **Pending** | GitHub issue #90 |

## Deployment gate

For an internal tester to reach Groq, all of these must be true:

```env
AI_CHAT_ENABLED=true
AI_CHAT_EXTERNAL_PROVIDER_ENABLED=true
GROQ_ZDR_CONFIRMED=true
GROQ_API_KEY=<secret>
GROQ_MODEL=openai/gpt-oss-120b
```

and the Telegram chat must be `ADMIN_ID` or appear in `AI_CHAT_BETA_IDS`.

Use `/ai_status` as admin to verify only the non-secret readiness flags. It intentionally does not print the API key or tester IDs.

## Not approved by this review

This snapshot does not conclude that a US external AI transfer is lawful for any particular Russian user or operator, does not replace required notifications/consents/contracts, and does not authorize public traffic.

Public free-form AI remains blocked until the pending matrix items are resolved and #90 P0 gates are green.
