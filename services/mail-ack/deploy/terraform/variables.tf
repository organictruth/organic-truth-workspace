variable "project_id" {
  type        = string
  description = "Existing GCP project. This stack does not create a project or link billing."
  default     = ""
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "image" {
  type        = string
  description = "Artifact Registry image URL. Required only when confirm_billable_apply is true."
  default     = ""
}

variable "confirm_billable_apply" {
  type        = bool
  description = "Must be set true by the parent after safety validation. Default refuses to create resources."
  default     = false
}

variable "create_firestore_database" {
  type        = bool
  description = "Create the project's single free-tier Firestore database. Set false if one already exists."
  default     = true
}

variable "push_audience" {
  type        = string
  description = "OIDC audience, normally the Cloud Run URL from the first apply. Empty keeps push fail-closed."
  default     = ""
}
