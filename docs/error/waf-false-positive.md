# AWS WAF 오탐으로 인한 정상 트래픽 차단

---

## 상황

ALB 로그에서 `/.env`, ThinkPHP/Log4j RCE 패턴 등 스캐너 공격 시도가 확인됨 (실제로는 Spring Security가 이미 401로 정상 차단 중이었음). 예방 차원에서 AWS WAF(WAFv2 Regional)를 ALB에 도입.

**탐지 로그 예시:**

```json
2026-04-30T04:21:00.805Z  WARN 1 --- [nio-8080-exec-5] c.everybuddy.global.util.CustomLogger    : 인증 실패 - ErrorCode: JWT_ENTRY_POINT, URI: /robots.txt
2026-04-30T04:19:04.770Z  WARN 1 --- [nio-8080-exec-1] c.everybuddy.global.util.CustomLogger    : 인증 실패 - ErrorCode: JWT_ENTRY_POINT, URI: /app/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php
```

**로그 분석 — 요청 경로별 공격 패턴 분류:**

| 요청 경로 | 공격 유형 |
| --- | --- |
| `/.env`, `/.git/config` | 환경변수·Git 설정 탈취 시도 |
| `/vendor/phpunit/.../eval-stdin.php` | PHP 원격코드실행(RCE) 공격 |
| `/owa/auth/`, `/ecp/...` | MS Exchange 서버 취약점 스캔 |
| `/global-protect/`, `/ssl-vpn/`, `/dana-na/` | Fortinet/Pulse VPN 취약점 스캔 |
| `/containers/json` | Docker API 무단접근 시도 |
| `/v1/pods`, `/version` | Kubernetes API 스캔 |
| `/index.php?s=/index/\think\app/invoke...` | ThinkPHP RCE 공격 (Tomcat에서 bad request로 차단됨) |
| `/admin/config.php`, `/jira/secure/...` | 관리자 페이지 스캔 |

모든 요청이 `ErrorCode: JWT_ENTRY_POINT`(401)로 이미 정상 차단되고 있었음 — 즉 애플리케이션 레벨에서는 실질적 위협이 아니었으나, 방어 계층을 하나 더 두기 위해 WAF 도입을 결정.

이후 번역 API(`/api/v1/translate/speech` 등)의 정상적인 음성·영상 업로드 요청이 403으로 차단되는 문제가 발생.

---

## 판단 및 근거

1. WAF 관리형 규칙(`CommonRuleSet`, `KnownBadInputsRuleSet`, `AmazonIpReputationList`, `AnonymousIpList`) + 커스텀 `RateLimitPerIP`(5분당 약 200요청) 구성으로 시작
2. 403 발생 시, 현재 `/api/v1/translate` 요청에 대해 body 크기 제한이 WAF 단에 있는지부터 확인하는 방식으로 원인 범위를 좁혀감
3. 원인이 관리형 `CommonRuleSet`의 기본 `SizeRestrictions_BODY` 규칙(기본 8KB 제한)이 정상적인 대용량 업로드까지 차단하고 있다는 것으로 좁혀짐
4. 보안 규칙 전체를 완화하는 대신, 우선순위가 더 높은 허용 규칙(`AllowBinaryUploadEndpoints`)을 추가하거나 해당 룰을 필요한 엔드포인트에서만 block → count로 조정하는 방식을 선택 — 나머지 엔드포인트의 보호 수준은 그대로 유지
5. 요청 크기 한도를 최대 약 100KB까지 허용하도록 단계적으로 조정. `/api/v1/translate/text` 등 신규 엔드포인트가 추가될 때마다 같은 문제가 재발해 반복적으로 튜닝(v2.9 ~ v3.1)

---

## 결과

403으로 막히던 정상 번역 요청 정상화.

보안 규칙을 전체 완화하지 않고 엔드포인트별 예외로 좁혀 해결함으로써, 오탐 이후에도 나머지 트래픽에 대한 WAF 보호 수준을 유지.

---

## 관련 문서

- [v2.7.0](../v2.7.0.md) — WAF(Web Application Firewall) 추가
- [v2.9.0](../v2.9.0.md) — WAF 번역 엔드포인트 허용
- [v3.0.0](../v3.0.0.md) — WAF 업로드 허용 확장
- [v3.1.0](../v3.1.0.md) — WAF 영상 업로드 허용
