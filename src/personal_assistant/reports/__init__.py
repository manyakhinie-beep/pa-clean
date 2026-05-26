"""
reports — Report generation and persistence for pa-merge.

Public interface
----------------
  POST /api/v1/reports/generate  — generate daily_agenda / completed_review / weekly_review
  GET  /api/v1/reports           — list persisted reports (newest first)
  GET  /api/v1/reports/{id}      — fetch a single report by short-id
  DELETE /api/v1/reports/{id}    — delete a report

Storage: JSON file at <vault_parent>/data/reports.json
"""
