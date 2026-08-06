variable "workspace_id" {
  description = "Fabric workspace to create the eventhouse in. Get it from the workspace URL, or the fabric_workspace resource."
  type        = string
}

variable "resource_group_name" {
  description = "Existing Azure resource group for the managed identity. The eventhouse itself lives in Fabric, not in Azure."
  type        = string
}

variable "location" {
  description = "Azure region for the managed identity."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric GitHub organization or enterprise ID."
  type        = string
}

variable "github_owner_type" {
  description = "organization or enterprise."
  type        = string
  default     = "organization"
}

variable "eventhouse_name" {
  description = "Eventhouse display name."
  type        = string
  default     = "actions-data-stream"
}

variable "database_name" {
  description = "KQL database name."
  type        = string
  default     = "ActionsDataStream"
}

variable "minimum_consumption_units" {
  description = <<-DESC
    Capacity units kept warm. 0 lets the eventhouse suspend when idle, which is
    the cheap default, at the cost of a 5-10 second cold start on the first
    query. Non-zero keeps it hot. Accepted values are 0, 2.25, 4.25, 8.5, 13,
    18, 26, 34, 50, or any number from 51 to 322.

    Changing this replaces the eventhouse.
  DESC
  type        = number
  default     = 0
}

variable "table_name" {
  description = "Table that events are ingested into."
  type        = string
  default     = "ActionsEvents"
}

variable "mapping_name" {
  description = "JSON ingestion mapping name."
  type        = string
  default     = "ActionsEventsMapping"
}

variable "ingestion_type" {
  description = <<-DESC
    streaming or queued.

    Fabric does not document whether the streaming endpoint needs enabling the
    way an ADX cluster does, so this module always applies the per-table
    streaming ingestion policy when streaming is selected. That covers both
    cases: required and applied, or not required and harmless.

    If streaming still fails, fall back to queued -- it uses a different
    endpoint with no policy dependency.
  DESC
  type        = string
  default     = "streaming"

  validation {
    condition     = contains(["streaming", "queued"], var.ingestion_type)
    error_message = "ingestion_type must be streaming or queued."
  }
}

variable "hot_cache_period" {
  description = "How long data stays in hot cache, as a KQL timespan. Fabric sets this with a database policy rather than a resource property, so this is `31d` and not the ISO 8601 `P31D` the azure-kusto module takes."
  type        = string
  default     = "31d"
}

variable "soft_delete_period" {
  description = "How long data is retained, as a KQL timespan."
  type        = string
  default     = "365d"
}

variable "identity_name" {
  description = "Name of the user-assigned managed identity."
  type        = string
  default     = "actions-data-stream"
}

variable "create_ingestor_grant" {
  description = <<-DESC
    Run `.add database <db> ingestors` for the stream identity.

    Needs Database Admin, which Fabric workspace Admin, Member and Contributor
    all inherit. Set false if you deploy as someone with less, then hand the
    ingestor_grant_command output to someone who has it.
  DESC
  type        = bool
  default     = true
}

variable "viewer_group_object_id" {
  description = <<-DESC
    Object ID of an Entra security group to grant Viewer on the database.

    Fabric item permissions and Kusto database ACLs are separate systems --
    sharing the eventhouse in the Fabric portal does not grant query access, and
    the failure surfaces as a bare "Access denied" naming only a principal GUID.
    Setting this closes that gap explicitly.

    Must be security-enabled. Kusto rejects Microsoft 365 / Unified groups.
  DESC
  type        = string
  default     = null
}

variable "create_analytics" {
  description = "Create the silver/gold analytics layer: Jobs and Runs materialized views, JobFacts() and RunFacts() functions, and an OtelSpans() function that emits OTel cicd.* spans. Shared with the azure-kusto module -- see modules/azure-kusto/kql/analytics.kql."
  type        = bool
  default     = true
}

variable "create_enrichment" {
  description = "Create the enrichment layer: WorkflowSteps and WorkflowLogs tables plus StepFacts(), StepStats(), JobTimeSplit() and friends. Schema only -- populate it with scripts/ingest-steps.py and scripts/ingest-logs.py. Shared with the azure-kusto module -- see modules/azure-kusto/kql/enrichment.kql."
  type        = bool
  default     = true
}

variable "enrichment_subjects" {
  type        = list(string)
  default     = []
  description = <<-DESC
    OIDC subject claims allowed to run the enrichment scripts in scripts/, which
    backfill the names, step rows and log bodies the stream itself does not
    carry. Leave empty and no enrichment identity is created.

    These are ordinary GitHub Actions OIDC subjects, so they are scoped to a repo
    and a ref, environment or tag:

      repo:my-org/ci-observability:ref:refs/heads/main
      repo:my-org/ci-observability:environment:production

    A second identity is created rather than reusing the stream's, because these
    scripts must *read* the raw events and the stream itself never should. See
    examples/enrichment-workflow.yml for the workflow side.
  DESC

  validation {
    condition     = alltrue([for s in var.enrichment_subjects : can(regex("^repo:[^/]+/[^:]+:(ref:.+|environment:.+|pull_request)$", s))])
    error_message = "Each subject must look like repo:OWNER/REPO:ref:refs/heads/BRANCH (or :environment:NAME, or :pull_request). A bare owner/repo is not a subject claim."
  }
}

variable "tags" {
  description = "Tags applied to the Azure identity resources. Fabric items do not take Azure tags."
  type        = map(string)
  default     = {}
}
