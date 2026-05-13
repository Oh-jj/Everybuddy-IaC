# everybuddy Infra

AWS 기반 everybuddy 서비스 인프라를 Terraform으로 관리하는 레포지토리입니다.

---

## 최신 패치 — v2.9.0

**날짜:** 2026-05-13 · **브랜치:** `dev`

🔧 Bastion(t3.nano→t3.micro) · Private Backend(t3.small→t3.medium) 인스턴스 스펙 상향 / WAF `AllowTranslateEndpoints` 규칙 추가 — 번역 API(/speech, /text)를 CommonRuleSet Body 크기 검사 이전에 명시적 허용

> 전체 변경 이력은 [docs/](./docs/) 참고

---

## 인프라 구조

**Provider:** AWS ap-southeast-1 (Singapore)
**도메인:** everybuddy.cloud

```
                        인터넷
                          │
              ┌───────────┴───────────┐
              │                       │
        HTTPS (443)              SSH (22)
              │                       │
              ▼                       ▼
   ┌─────────────────┐    ┌─────────────────────┐
   │   ALB            │    │  Bastion EC2         │
   │  everybuddy-alb  │    │  public-b / AZ-b     │
   │  (internet-facing│    │                      │
   │   AZ-a + AZ-b)  │    │  t3.micro            │
   └────────┬────────┘    └──────────┬──────────┘
            │                        │
     HTTP:8080                 SSH ProxyJump
            │                        │
            ▼                        ▼
   ┌──────────────────────────────────────────┐
   │         Private App Subnet               │
   │         10.0.11.0/24 (AZ-a)             │
   │                                          │
   │   EC2 everybuddy-private-backend         │
   │   t3.medium                             │
   │   Spring Boot :8080                      │
   │   Node Exporter :9100                    │
   │   Promtail → Loki                        │
   └───────────┬──────────────────────────────┘
               │
        ┌──────┴──────┐
        │             │
    MySQL:3306    NAT GW → 인터넷
        │         (DockerHub, S3, Firebase)
        ▼
   ┌──────────────────────────┐
   │  Private DB Subnet       │
   │  10.0.21.0/24 (AZ-a)    │
   │                          │
   │  RDS everybuddy-mysql    │
   │  MySQL 8.0 / db.t3.micro │
   └──────────────────────────┘


   ┌──────────────────────────────────┐
   │  Public Subnet 10.0.2.0/24 (AZ-a)│
   │                                  │
   │  EC2 everybuddy-monitoring        │
   │  t3.micro                        │
   │  ├── Grafana      :3000          │
   │  ├── Prometheus   :9090          │
   │  └── Loki         :3100          │
   └──────────────┬───────────────────┘
                  │ ▲ Loki :3100
                  │ │ (Promtail 로그 푸시)
   ┌──────────────┴───────────────────┐
   │  외부 GPU 서버 (laurel)           │
   │  Promtail → 로그 수집            │
   └──────────────────────────────────┘
```

---

## 리소스 목록

### EC2

| 이름                       | 타입     | IP      | 서브넷            | 역할                         |
| -------------------------- | -------- | ------- | ----------------- | ---------------------------- |
| everybuddy-private-backend | t3.medium | private | private-app-a     | Spring Boot API 서버         |
| everybuddy-monitoring      | t3.micro  | public  | public-monitoring | Grafana / Prometheus / Loki  |
| everybuddy-bastion         | t3.micro  | public  | public-b          | SSH 접근 및 CI/CD 게이트웨이 |

### 네트워크

| 리소스                     | 이름           | CIDR / 값           |
| -------------------------- | -------------- | ------------------- |
| VPC                        | everybuddy-vpc | 10.0.0.0/16         |
| Subnet (public-backend)    | -              | 10.0.1.0/24, AZ-a   |
| Subnet (public-monitoring) | -              | 10.0.2.0/24, AZ-a   |
| Subnet (public-b)          | -              | 10.0.3.0/24, AZ-b   |
| Subnet (private-app-a)     | -              | 10.0.11.0/24, AZ-a  |
| Subnet (private-app-b)     | -              | 10.0.12.0/24, AZ-b  |
| Subnet (private-db-a)      | -              | 10.0.21.0/24, AZ-a  |
| Subnet (private-db-b)      | -              | 10.0.22.0/24, AZ-b  |
| NAT Gateway                | -              | public-backend AZ-a |

### ALB / DNS / 인증서

| 리소스       | 값                                            |
| ------------ | --------------------------------------------- |
| ALB          | everybuddy-alb (internet-facing, AZ-a + AZ-b) |
| 도메인       | api.everybuddy.cloud → ALB Alias              |
| ACM          | everybuddy.cloud (ISSUED)                     |
| Route53 Zone | everybuddy.cloud                              |

### RDS

| 항목       | 값                  |
| ---------- | ------------------- |
| Identifier | everybuddy-mysql    |
| Engine     | MySQL 8.0           |
| Class      | db.t3.micro         |
| Subnet     | private-db-a (AZ-a) |

### S3

| 버킷                       | 용도                           |
| -------------------------- | ------------------------------ |
| everybuddy-files (prod)    | 파일 업로드/다운로드 스토리지  |
| everybuddy-terraform-state | Terraform Remote Backend State |

---

## 모듈 구조

```
modules/
├── networking/   # VPC, Subnet, IGW, NAT GW, Route Table
├── security/     # Security Groups
├── compute/      # EC2, Key Pair, EIP
├── storage/      # S3
├── database/     # RDS, DB Subnet Group
├── dns/          # Route53 Hosted Zone
├── alb/          # ALB, ACM, Target Group, Listeners, Route53 A Record
└── waf/          # WAF v2 (IP Reputation, AllowTranslate, CommonRuleSet, KnownBadInputs)
```

---

## CI/CD

GitHub Actions → Bastion ProxyJump → Private Backend

```
Push to main
  └── GitHub Actions
        ├── Docker build & push (Docker Hub)
        ├── SCP docker-compose.yml → Bastion → Private Backend
        └── SSH → Bastion → Private Backend → docker compose up -d
```

---

## 패치 이력

| 버전                       | 날짜       | 내용                                   |
| -------------------------- | ---------- | -------------------------------------- |
| [v2.9.0](./docs/v2.9.0.md) | 2026-05-13 | 인스턴스 스펙 상향 + WAF 번역 엔드포인트 허용 |
| [v2.8.0](./docs/v2.8.0.md) | 2026-05-09 | 외부 GPU 서버 → Loki 로그 수집 허용    |
| [v2.7.0](./docs/v2.7.0.md) | 2026-04-30 | WAF(Web Application Firewall) 추가     |
| [v2.6.1](./docs/v2.6.1.md) | 2026-03-18 | 보안: .claude/ gitignore 처리          |
| [v2.6.0](./docs/v2.6.0.md) | 2026-03-13 | S3 Remote Backend 설정                 |
| [v2.5.0](./docs/v2.5.0.md) | 2026-03-13 | 모니터링 SG 보완                       |
| [v2.4.0](./docs/v2.4.0.md) | 2026-03-11 | 백엔드 서버 Private 전환               |
| [v2.3.0](./docs/v2.3.0.md) | 2026-03-11 | ALB + ACM (HTTPS) 구성                 |
| [v2.2.0](./docs/v2.2.0.md) | 2026-03-10 | Bastion 서버 + Route53 구성            |
| [v2.1.0](./docs/v2.1.0.md) | 2026-03-10 | RDS MySQL 구성                         |
| [v2.0.0](./docs/v2.0.0.md) | 2026-03-10 | Terraform 모듈화 + Private 서브넷 준비 |
| [v1.2.0](./docs/v1.2.0.md) | 2026-02-05 | 백엔드 스펙 업그레이드 + EIP           |
| [v1.1.0](./docs/v1.1.0.md) | 2026-01-13 | 모니터링 서버 추가                     |
| [v1.0.0](./docs/v1.0.0.md) | 2026-01-11 | 초기 인프라 구성                       |
