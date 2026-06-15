resource "aws_s3_bucket" "datalake" {
  bucket = "${var.project_name}-datalake-${var.suffix}"

  tags = {
    Name    = "${var.project_name}-datalake"
    Purpose = "laurel GPU 에러 이력 저장"
  }
}

resource "aws_s3_bucket_versioning" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowLambdaWrite"
        Effect    = "Allow"
        Principal = { AWS = var.lambda_role_arn }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.datalake.arn}/laurel-errors/*"
      }
    ]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  rule {
    id     = "laurel-errors-retention"
    status = "Enabled"

    filter {
      prefix = "laurel-errors/"
    }

    # 1년 후 Glacier로 전환 (조회 빈도 낮아지는 시점)
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
  }
}
