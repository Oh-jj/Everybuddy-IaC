output "bucket_name" {
  description = "Data Lake S3 버킷명"
  value       = aws_s3_bucket.datalake.bucket
}

output "bucket_arn" {
  description = "Data Lake S3 버킷 ARN (IAM 정책용)"
  value       = aws_s3_bucket.datalake.arn
}
