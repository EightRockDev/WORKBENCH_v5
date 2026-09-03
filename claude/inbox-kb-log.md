# Inbox → KB delivery log

Standing rule (owner directive 2026-08-11): every analysis session that sweeps
mail appends its delivery status here. Lane 2 = git intake
(`data/inbox_kb_intake/` on origin/main; `ingest_git_intake` lands them
host-side). Host verification: `reports/inbox-sync-latest.txt` counts.

## 2026-09-03 (cloud session)

- Swept Gmail (bmccune@gmail.com) + O365 (Brian@eight-rock.com), window
  2026-08-27 → 2026-09-03.
- Delivered **24** deal-mail JSONs via the git intake lane (lane 2), curated
  fields (confidence 0.9), external_id = internet message id, one record per
  property (broker re-sends deduped).
- Skipped: newsletters (GlobeSt, Best Ever CRE, LinkedIn), vendor/marketing
  mail, personal/finance mail, VDR-invite and document-alert process mail,
  and active-deal closing correspondence (Crossroads — already a live deal,
  not a new listing).
- Verify next cycle: `reports/inbox-sync-latest.txt` should show 24 files
  ingested from git intake. No mailbox connectors in the app yet, so lane 2
  remains the working path.
