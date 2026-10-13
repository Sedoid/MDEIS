"""The database reads and writes the tools share: problems, and the approver's answers.

A problem found in a learner's cards gets a stable issue number the first time it is seen
(`I-000045`). Finding it again returns the same number, because the number is tied to a fingerprint
of what the problem is. Answers are only ever added, never changed.
"""


def sync_issues(conn, learner_id, issues, run_id) -> None:
    """Give every problem its issue number, saving the ones not seen before.

    Sets issue["issue_id"] on each one.
    """
    for issue in issues:
        row = conn.execute(
            "SELECT issue_id FROM issues WHERE fingerprint = %s", (issue["fingerprint"],)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "INSERT INTO issues (learner_id, fingerprint, kind, severity, subject, record_ids, "
                "detail, question, run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (fingerprint) DO NOTHING RETURNING issue_id",
                (
                    learner_id,
                    issue["fingerprint"],
                    issue["kind"],
                    issue["severity"],
                    issue["subject"],
                    issue["records"],
                    issue["detail"],
                    issue["question"],
                    run_id,
                ),
            ).fetchone()
        if row is None:  # another run saved it a moment ago
            row = conn.execute(
                "SELECT issue_id FROM issues WHERE fingerprint = %s", (issue["fingerprint"],)
            ).fetchone()
        issue["issue_id"] = row[0]


def load_answers(conn, learner_id) -> dict:
    """The latest answer to each of the learner's problems: {issue_id: {...}}."""
    rows = conn.execute(
        "SELECT DISTINCT ON (r.issue_id) r.issue_id, r.resolution, r.value, r.note, "
        "r.approver_name "
        "FROM resolutions r JOIN issues i USING (issue_id) WHERE i.learner_id = %s "
        "ORDER BY r.issue_id, r.resolution_id DESC",
        (learner_id,),
    ).fetchall()
    return {
        issue_id: {"resolution": resolution, "value": value, "note": note, "approver": approver}
        for issue_id, resolution, value, note, approver in rows
    }


def learner_exists(conn, learner_id) -> bool:
    found = conn.execute("SELECT 1 FROM learners WHERE learner_id = %s", (learner_id,)).fetchone()
    return found is not None
