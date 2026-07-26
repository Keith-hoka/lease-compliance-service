from datetime import date


def diff_findings(old: list[dict], new: list[dict]) -> dict[str, dict]:
    """Rules whose verdict differs between two findings lists.

    A rule present on one side only reports None for the absent side.
    """
    old_verdicts = {f["rule_id"]: f["verdict"] for f in old}
    new_verdicts = {f["rule_id"]: f["verdict"] for f in new}
    return {
        rule_id: {"from": old_verdicts.get(rule_id), "to": new_verdicts.get(rule_id)}
        for rule_id in old_verdicts.keys() | new_verdicts.keys()
        if old_verdicts.get(rule_id) != new_verdicts.get(rule_id)
    }


def new_version_dates(timeline: list[date], ingested: set[date]) -> list[date]:
    """Timeline dates not yet ingested, ascending."""
    return sorted(set(timeline) - ingested)
