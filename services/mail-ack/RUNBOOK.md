# Mail acknowledgement runbook

Isolated service for one automatic note to people who write the Organic Truth business mailbox. It does not touch the storefront, catalog, or prices.

**This change is tested code and unapplied infrastructure. It is not a live deployment, and it has not sent mail.**

## Status

| Item | State |
| --- | --- |
| Eligibility, provenance, dedup, cap, and crash behavior | Tested offline in this repository |
| Sending | Off. Both the enable flag and the owner activation are unset |
| Cloud Run, Pub/Sub, Firestore, Scheduler, Secret Manager, Artifact Registry | Described in Terraform. Not created by this change |
| Gmail watch | Not registered |
| OAuth token, ARC public-key pin, OIDC verification against live Google | Not provisioned |
| Owner approval for the live setup | Already given on 7 Nov 2026. This runbook does not ask for that approval again |

A prior local prototype was staged and not deployed. It is not imported here. Its checks (authserv-id text matching `mx.google.com`, a single-value header map, and no durable cap) are not used. The tests in this tree are necessary regression checks. They do not prove that a live Google message, a live Firestore project, or a live IAM policy behaves as the fakes do.

## What the service will do once the parent enables it

Gmail publishes a small push (mailbox identity and a history id) to Pub/Sub. Pub/Sub POSTs that notice to Cloud Run with an OIDC token. The service lists Gmail history since its stored cursor, fetches each new message, and decides whether to send one fixed note:

> Hi, thank you for writing to Organic Truth. This is an automatic note to confirm we received your message. A member of our team will read it and reply personally. If it is about an order, including your order number helps us find it. Organic Truth LLC

There is no model-written text and no unsubscribe block. The note goes to the `From` address only. `Reply-To`, `Cc`, and `Bcc` are ignored. The personal alias `organictruthllc@gmail.com` (and `organictruthllc@googlemail.com`) is never a recipient. Mail that is not eligible stays in the mailbox. Nothing is deleted, archived, or labeled.

Push is not zero latency. Gmail recommends renewing `users.watch` daily because a watch expires after seven days. If a push is missed, the daily job renews the watch and reconciles history. The first time the service sees a mailbox it stores the current history id and does not acknowledge older mail.

## Security boundary

A message is eligible only when all of the following are true:

- The bytes were fetched by the Gmail client for this mailbox. A header that claims to be a Gmail fetch is ignored.
- Exactly one ARC seal is present, with `i=1`, `cv=none`, `d=google.com`, and `a=rsa-sha256`, and the signature verifies under the pinned public key for the seal's selector. The pin is required. A missing pin, a bad signature, or an unpinned selector fails closed.
- The DMARC result is read only from that sealed `ARC-Authentication-Results` value, after comments are removed, as a structured method. `dmarc=pass` must be a real method result, and `dkim` or `spf` must also be `pass`. An `Authentication-Results` header is never enough, even when its authserv-id is `mx.google.com`.
- Exactly one `Delivered-To`, and it appears before the ARC seal, and it is exactly `organictruth@organictruthllc.com`. A later `Delivered-To` does not grant delivery. Two values, the personal alias, or a missing value fail closed.
- A `Received` header before `From` has a `by` host of exactly `mx.google.com`. A mention of that host in another header does not count.
- The first `Return-Path` before `From` exists and is a real mailbox. Missing or `<>` fails closed.
- `To` is only the business address. The personal alias in `To`, `Cc`, or delivery headers fails closed.
- The sender is one external mailbox, aligned with the sealed `header.from`, and is not a no-reply, bounce, own-domain, or alias address.
- No bulk, list, forward, autoresponder, or loop header, and no spam or bulk Gmail label.
- Subject and text are within the injury, bank, payment, legal, medical, and security exclusion lists. Those lists can only deny. The word "order" is not an exclusion. Keywords are not what makes mail eligible.

Header fields are kept as an ordered list. Duplicate `Authentication-Results` or `Delivered-To` values are not collapsed.

Known narrow limit: mail that already has an ARC chain (`i` greater than 1) is left for a person, even when Google would have added a later seal. That is fail-closed, not a claim that every legitimate customer message will be acknowledged.

## Crash and duplicates

The service does not claim exactly-once delivery across Gmail's send API and the database.

| Stored state | Meaning | On another delivery of the same Gmail id |
| --- | --- | --- |
| `reserved` | Eligible and not yet handed to Gmail | May send once |
| `send_attempted` | The send call was about to start, or a process died in that window | Do not send again |
| `sent` | Gmail returned a message id | Do not send again |
| `send_rejected` | Gmail returned a definitive 4xx | Do not send again |
| `send_uncertain` | Timeout, 5xx, or a crash after the attempt marker | Do not send again |
| `held_switch_off` | Seen while sending was off | Do not send later |

A provider timeout can mean the note was accepted and the response was lost. Leaving that row uncertain can drop one acknowledgement. Retrying it could send a second note. The service keeps the dropped acknowledgement.

One sender (plus-tags removed; dots removed only for gmail.com) can cause at most one note every four days. A thread is acknowledged once. The twentieth send attempt in a UTC day is allowed and then a latch trips. Further attempts are not sent until `python -m mail_ack clear-cap-latch`. The latch is a record in the store and a local status file. It does not email, page, or call a webhook. Clearing the latch does not turn sending on.

SQLite is the reference store used by the tests, including concurrent workers. Cloud Run disk is ephemeral, so the deployed configuration uses Firestore transactions and the same transition functions. The Firestore HTTP client is tested against a local fake, not against a live Google project. The parent should run that path against the Firestore emulator before turning sending on.

## Costs

Figures below were read on 7 Nov 2026 from the Google Cloud pricing pages named in `mail_ack/costs.py`. They are list prices in USD, not a quote, and Google can change them. Free allotments are shared by the whole billing account (Logging's 50 GiB is per project). This stack does not create a budget and does not change billing.

Planning scenario: 1,500 pushes a month, 30 daily jobs, 1 vCPU and 512 MiB, about one active second per request, 1 KiB Pub/Sub messages with one delivery, a 0.2 GiB image, two secret versions, one Scheduler job, and about 0.15 GiB of fetched mail. Region `us-central1`. Request-based Cloud Run with minimum instances 0. No load balancer, NAT, VPC connector, Cloud SQL, GPU, Firestore PITR, or backup.

| Component | Published price used | This scenario if the free allotment is still available | If that allotment is already used |
| --- | --- | --- | --- |
| Gmail API | No usage fee found | $0 | $0 |
| Cloud Run request-based | $0.000024 / vCPU-second, $0.0000025 / GiB-second, $0.40 / million requests after 180,000 vCPU-seconds, 360,000 GiB-seconds, and 2 million requests | $0 | about $0.039 |
| Pub/Sub Message Delivery Basic | First 10 GiB / month, then $40 / TiB | $0 | about $0.0001 |
| Firestore Standard, Iowa | $0.03 / 100k reads and $0.09 / 100k writes after 50k reads and 20k writes per day; storage about $0.000205479 / GiB-hour after 1 GiB | $0 | about $0.013 |
| Cloud Scheduler | 3 jobs free, then $0.10 / job-month | $0 | $0.10 |
| Secret Manager | 6 active versions and 10,000 accesses free, then about $0.000082192 / version-hour | $0 | about $0.12 |
| Artifact Registry | 0.5 GiB free, then about $0.000136986 / GiB-hour | $0 | about $0.02 |
| Cloud Build e2-standard-2 | 2,500 minutes free, then $0.006 / minute | $0 | about $0.03 for a 5-minute build |
| Cloud Logging | 50 GiB / project free, then $0.50 / GiB | $0 | about $0.05 |
| IAM | No charge | $0 | $0 |

Modeled total if every free allotment is already consumed: **about $0.37 per month**. If the allotments are free, the incremental total is **$0**. Cloud Run's pricing page includes 1 GiB of North America internet egress. This scenario's 0.15 GiB stays inside that allotment. Confirm on the billing catalog whether Gmail API responses are metered as that egress; the pricing page does not say so explicitly.

Not in this design, and the reason they are omitted: a regional external load balancer is commonly on the order of $18 per month before data, and the smallest always-on Cloud SQL instance is several dollars per month before storage. Minimum instances above zero would bill idle CPU. Pub/Sub snapshots and retained acknowledged messages are about $0.27 / GiB-month. Firestore PITR and backups have no free tier. None of those are in the Terraform.

A billing account must still be linked before these APIs will enable, even when the expected charge is $0. Linking it is a parent action. This change does not do it.

## Unavoidable prerequisites

These are technical steps for the parent. They are not a second approval of the 7 Nov 2026 decision.

1. **Billing account.** The project must already be allowed to enable Cloud Run, Pub/Sub, Firestore, Cloud Scheduler, Secret Manager, and Artifact Registry. This stack does not attach a billing account.
2. **Workspace organization.** `organictruthllc.com` publishes MX `smtp.google.com`, which is consistent with Google-hosted mail. An Internal OAuth client is available only when the Cloud project is inside the same Workspace organization as the mailbox. Use Internal. Do not create an External client in Testing; those refresh tokens expire after seven days. Do not fall back to that token.
3. **Existing OAuth grant.** Required scopes are `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/gmail.send`. `users.watch` accepts the readonly scope. If a token with those scopes already exists, reuse it. Do not request `https://mail.google.com/` and do not ask the owner to consent again for the same access. Domain-wide delegation is not required.
4. **Gmail publisher on the topic.** `gmail-api-push@system.gserviceaccount.com` needs `roles/pubsub.publisher`. If the organization blocks members outside the domain, an admin has to allow that Google-managed service account. That exception is only required when the org policy actually blocks it.
5. **Secret versions, outside git.** A Gmail token file and an ARC public-key pin. The service refuses a private key in the pin file. Retrieve Google's current ARC key out of band (`dig TXT` for the selector on the seal, commonly `arc-20160816._domainkey.google.com`) and store only the public key. When Google rotates the selector, verification fails closed until the pin is updated. DNS at request time is not trusted.
6. **Second apply for the audience.** Cloud Run's URL exists only after the first apply. Set `push_audience` to that URL so Pub/Sub and Scheduler OIDC match. Until then the app returns 401. Do not grant public invoke.
7. **Firestore emulator check** of the HTTP adapter before sending is turned on. Concurrency of the state machine is already tested on SQLite.

No customer message and no secret belongs in this repository.

## Deploy, without doing it here

From `services/mail-ack/deploy/terraform`, with `confirm_billable_apply` left false, `terraform plan` shows no billable resources. The parent, after validation:

1. Build the image from `services/mail-ack` and push it to Artifact Registry in `us-central1`.
2. Add the two Secret Manager versions. Do not put those values in Terraform.
3. Apply with `confirm_billable_apply=true`, the project id, and the image. Leave `push_audience` empty on that apply.
4. Set `push_audience` to the `service_uri` output and apply again. That creates the push subscription and the one daily Scheduler job.
5. Call `users.watch` only through `python -m mail_ack renew-watch` once the token and topic exist. Sending is still off, so maintenance will record history and will not send.
6. Watch logs for reason codes only. Logs are not supposed to contain message bodies.

Destroying the Firestore database requires turning off its delete protection first. That protection is intentional so a destroy does not throw away the dedup record.

## Turning sending on

Do this only after the parent has checked the runtime, Google access, and billing. It is not part of this change. Both values are required, on the Cloud Run revision, and a later apply of the current Terraform would set them back to off:

- `MAIL_ACK_SENDING_ENABLED=true`
- `MAIL_ACK_OWNER_ACTIVATION=owner-activated-2026-11-07`

A kill file (`MAIL_ACK_KILL_FILE`) forces the process off where that file is visible. On Cloud Run the durable stops are the environment flags and the store latch. `python -m mail_ack clear-cap-latch` clears a cap or operator latch and still does not enable sending by itself.

## Local checks

```bash
PYTHONPATH=services/mail-ack python3 -m unittest discover -s services/mail-ack/tests -v
PYTHONPATH=services/mail-ack python3 -m mail_ack self-check
```

`self-check` prints whether sending is configured. It does not contact Google.
