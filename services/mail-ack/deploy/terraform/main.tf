# Creates billable resources only when confirm_billable_apply is true.
# This repository does not run terraform apply and does not link a billing account.
# Sending is hardcoded off. Enabling it is a later, separate change.

locals {
  enabled = var.confirm_billable_apply && var.project_id != "" && var.image != ""
  apis = [
    "run.googleapis.com",
    "pubsub.googleapis.com",
    "firestore.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "gmail.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "terraform_data" "apply_guard" {
  lifecycle {
    precondition {
      condition     = !var.confirm_billable_apply || (var.project_id != "" && var.image != "")
      error_message = "Refusing billable apply without project_id and image. Leave confirm_billable_apply false until the parent validates the service."
    }
  }
}

resource "google_project_service" "apis" {
  for_each           = local.enabled ? toset(local.apis) : toset([])
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "run" {
  count        = local.enabled ? 1 : 0
  account_id   = "mail-ack-run"
  display_name = "Organic Truth mail acknowledgement runtime"
}

resource "google_service_account" "push" {
  count        = local.enabled ? 1 : 0
  account_id   = "mail-ack-push"
  display_name = "Organic Truth mail acknowledgement Pub/Sub push"
}

resource "google_service_account" "sched" {
  count        = local.enabled ? 1 : 0
  account_id   = "mail-ack-sched"
  display_name = "Organic Truth mail acknowledgement daily task"
}

resource "google_artifact_registry_repository" "ack" {
  count         = local.enabled ? 1 : 0
  location      = var.region
  repository_id = "mail-ack"
  format        = "DOCKER"
  description   = "Mail acknowledgement image. No other services."
  depends_on    = [google_project_service.apis]
}

resource "google_firestore_database" "ack" {
  count                             = local.enabled && var.create_firestore_database ? 1 : 0
  name                              = "(default)"
  location_id                       = var.region
  type                              = "FIRESTORE_NATIVE"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_DISABLED"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  depends_on                        = [google_project_service.apis]
}

resource "google_secret_manager_secret" "gmail" {
  count     = local.enabled ? 1 : 0
  secret_id = "mail-ack-gmail-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "arc" {
  count     = local.enabled ? 1 : 0
  secret_id = "mail-ack-arc-keys"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "gmail" {
  count     = local.enabled ? 1 : 0
  secret_id = google_secret_manager_secret.gmail[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run[0].email}"
}

resource "google_secret_manager_secret_iam_member" "arc" {
  count     = local.enabled ? 1 : 0
  secret_id = google_secret_manager_secret.arc[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run[0].email}"
}

resource "google_project_iam_member" "firestore" {
  count   = local.enabled ? 1 : 0
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.run[0].email}"
}

resource "google_pubsub_topic" "gmail" {
  count   = local.enabled ? 1 : 0
  name    = "mail-ack-gmail"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic_iam_member" "gmail_api" {
  count  = local.enabled ? 1 : 0
  topic  = google_pubsub_topic.gmail[0].name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
}

resource "google_cloud_run_v2_service" "ack" {
  count    = local.enabled ? 1 : 0
  name     = "mail-ack"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run[0].email
    timeout         = "180s"
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    containers {
      image = var.image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }
      env {
        name  = "MAIL_ACK_SENDING_ENABLED"
        value = "false"
      }
      env {
        name  = "MAIL_ACK_OWNER_ACTIVATION"
        value = ""
      }
      env {
        name  = "MAIL_ACK_STORE"
        value = "firestore"
      }
      env {
        name  = "MAIL_ACK_FIRESTORE_PROJECT"
        value = var.project_id
      }
      env {
        name  = "MAIL_ACK_BIND"
        value = "0.0.0.0"
      }
      env {
        name  = "MAIL_ACK_PUBSUB_TOPIC"
        value = "projects/${var.project_id}/topics/mail-ack-gmail"
      }
      env {
        name  = "MAIL_ACK_PUSH_AUDIENCE"
        value = var.push_audience
      }
      env {
        name  = "MAIL_ACK_INVOKER_EMAILS"
        value = "${google_service_account.push[0].email},${google_service_account.sched[0].email}"
      }
      env {
        name  = "MAIL_ACK_OIDC_JWKS_URL"
        value = "https://www.googleapis.com/oauth2/v3/certs"
      }
      env {
        name  = "MAIL_ACK_GMAIL_TOKEN_PATH"
        value = "/secrets/gmail/token.json"
      }
      env {
        name  = "MAIL_ACK_ARC_KEYS_PATH"
        value = "/secrets/arc/keys.json"
      }
      volume_mounts {
        name       = "gmail"
        mount_path = "/secrets/gmail"
      }
      volume_mounts {
        name       = "arc"
        mount_path = "/secrets/arc"
      }
    }
    volumes {
      name = "gmail"
      secret {
        secret = google_secret_manager_secret.gmail[0].secret_id
        items {
          version = "latest"
          path    = "token.json"
        }
      }
    }
    volumes {
      name = "arc"
      secret {
        secret = google_secret_manager_secret.arc[0].secret_id
        items {
          version = "latest"
          path    = "keys.json"
        }
      }
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "push" {
  count    = local.enabled && var.push_audience != "" ? 1 : 0
  name     = google_cloud_run_v2_service.ack[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.push[0].email}"
}

resource "google_cloud_run_v2_service_iam_member" "sched" {
  count    = local.enabled && var.push_audience != "" ? 1 : 0
  name     = google_cloud_run_v2_service.ack[0].name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.sched[0].email}"
}

resource "google_pubsub_subscription" "push" {
  count                      = local.enabled && var.push_audience != "" ? 1 : 0
  name                       = "mail-ack-gmail"
  topic                      = google_pubsub_topic.gmail[0].name
  ack_deadline_seconds       = 120
  retain_acked_messages      = false
  message_retention_duration = "86400s"
  expiration_policy {
    ttl = ""
  }
  push_config {
    push_endpoint = "${google_cloud_run_v2_service.ack[0].uri}/push"
    oidc_token {
      service_account_email = google_service_account.push[0].email
      audience              = var.push_audience
    }
  }
}

resource "google_cloud_scheduler_job" "daily" {
  count            = local.enabled && var.push_audience != "" ? 1 : 0
  name             = "mail-ack-daily"
  schedule         = "15 8 * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "180s"
  http_target {
    uri         = "${google_cloud_run_v2_service.ack[0].uri}/tasks/daily"
    http_method = "POST"
    oidc_token {
      service_account_email = google_service_account.sched[0].email
      audience              = var.push_audience
    }
  }
  depends_on = [google_project_service.apis]
}
