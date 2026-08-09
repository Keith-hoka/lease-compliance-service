"""Human-readable labels for corpus section keys.

The label derives from the section_no shape alone: plain numbers are Act or
Regulation sections, "S{sch}-{cl}" is a schedule clause, "S{sch}-T{n}" is an
NSW standard-form term, and "S{sch}-F{form}-T{n}" is a VIC prescribed-form
term. Jurisdictional part/division conventions never enter the label.
"""

import re

_FORM_TERM = re.compile(r"^S(\w+)-F(\w+)-T(\w+)$")
_SCHEDULE_TERM = re.compile(r"^S(\w+)-T(\w+)$")
_SCHEDULE_CLAUSE = re.compile(r"^S(\w+)-(\w+)$")


def format_citation(section_no: str) -> str:
    match = _FORM_TERM.match(section_no)
    if match:
        return f"Sch {match.group(1)} Form {match.group(2)} term {match.group(3)}"
    match = _SCHEDULE_TERM.match(section_no)
    if match:
        return f"Sch {match.group(1)} term {match.group(2)}"
    match = _SCHEDULE_CLAUSE.match(section_no)
    if match:
        return f"Sch {match.group(1)} cl {match.group(2)}"
    return f"s {section_no}"
