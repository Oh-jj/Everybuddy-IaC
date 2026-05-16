# ============================================================
# AWS WAF v2 - Regional (ALB 연결)
# ============================================================
resource "aws_wafv2_web_acl" "main" {
  name        = "${var.project_name}-waf"
  description = "WAF for ${var.project_name} ALB"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # ── Rule 0: Rate Limit (단일 IP 200건/5분 초과 시 차단) ────
  rule {
    name     = "RateLimitPerIP"
    priority = 0

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 200
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "RateLimitPerIP"
      sampled_requests_enabled   = true
    }
  }

  # ── Rule 1: IP Reputation (봇, 스캐너, 악성 IP 차단) ──────
  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "IpReputationList"
      sampled_requests_enabled   = true
    }
  }

  # ── Rule 2: 바이너리 업로드 엔드포인트 허용 (CommonRuleSet false positive 우회) ──
  # /api/v1/translate/speech : m4a 파일 업로드 (8KB 초과)
  # /api/v1/translate/text   : 긴 텍스트 번역 (최대 ~100KB)
  # /api/v1/users/me         : 프로필 이미지 업로드 (JPEG/PNG 바이너리 false positive)
  rule {
    name     = "AllowBinaryUploadEndpoints"
    priority = 2

    action {
      allow {}
    }

    statement {
      or_statement {
        statement {
          byte_match_statement {
            search_string = "/api/v1/translate/speech"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
            positional_constraint = "STARTS_WITH"
          }
        }
        statement {
          byte_match_statement {
            search_string = "/api/v1/translate/text"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
            positional_constraint = "STARTS_WITH"
          }
        }
        statement {
          byte_match_statement {
            search_string = "/api/v1/users/me"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
            positional_constraint = "STARTS_WITH"
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AllowBinaryUploadEndpoints"
      sampled_requests_enabled   = true
    }
  }

  # ── Rule 3: Common Rule Set (OWASP Top 10) ─────────────────
  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 3

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "CommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # ── Rule 4: Known Bad Inputs (PHP, Log4j, ThinkPHP 등) ─────
  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 4

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "KnownBadInputs"
      sampled_requests_enabled   = true
    }
  }

  # ── Rule 5: Anonymous IP (VPN, 프록시, Tor 출구 노드 차단) ─
  rule {
    name     = "AWSManagedRulesAnonymousIpList"
    priority = 5

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAnonymousIpList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AnonymousIpList"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project_name}-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name = "${var.project_name}-waf"
  }
}

# ── WAF → ALB 연결 ─────────────────────────────────────────
resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = var.alb_arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}
