output "service_uri" {
  value       = try(google_cloud_run_v2_service.ack[0].uri, null)
  description = "Set push_audience to this URL on a second apply so authenticated push can be verified."
}

output "apply_status" {
  value = "not applied by the repository change that added this stack"
}

output "sending" {
  value = "off"
}
