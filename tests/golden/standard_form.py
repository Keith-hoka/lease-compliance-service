"""Corpus-driven golden documents for the standard-form eval.

Documents are assembled from the prescribed texts themselves; placeholders
are filled with fixture values so verbatim baselines screen green.
"""

import re
from dataclasses import dataclass

FILLERS = {
    "amount": "$550.00",
    "date": "1 March 2026",
    "name": "Alex Tenant",
    "address": "1 Example Street, Sydney NSW 2000",
    # VIC F2 term 38 "Extension of term" states its own rule in the same
    # breath as its blank: the new end date "must be at least 5 years and
    # one day from the commencement date". Filling it with the same generic
    # FILLERS["date"] used for "date of agreement" elsewhere in the document
    # makes the rendered extension date equal the rendered commencement
    # date - a genuine violation of the term's own stated rule, which a
    # careful reader (the judge, correctly) flags as adverse. This is 5
    # years and a day past FILLERS["date"].
    "extended_date": "2 March 2031",
}

_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})


def _straighten(text: str) -> str:
    """Curly quotes/apostrophes to straight ones.

    The corpus preserves the source instruments' typographic punctuation,
    but a judge quoting from the rendered document consistently reproduces
    it with straight punctuation. screen_terms' own normalize() already
    treats the two as equivalent (so this has no effect on what screens
    green), but quote_matches() - used to verify a judged "covered" or
    "altered_adverse" outcome's lease_quote - is a plain substring check
    with no such normalisation, and a document that still carries curly
    punctuation can fail that check even when the quote is otherwise a
    faithful, verbatim excerpt (confirmed empirically: nsw.clause.sf_t36's
    alteration text carried a curly apostrophe from the corpus, and the
    judge's straight-apostrophe quote of the identical words was rejected).
    Golden documents render with straight punctuation throughout so this
    mismatch cannot arise.
    """
    return text.translate(_QUOTE_MAP)


def _fill(text: str) -> str:
    def repl(match: re.Match) -> str:
        inner = match.group(0).lower()
        if inner.startswith(("[if ", "[option ")):
            # A conditional gate ("[If the animal is a mammal...]") or a
            # labelled-alternative header ("[Option 1-Inflation...]") is
            # drafting guidance, not a blank to fill: a real, applicable
            # lease clause simply omits it, matching how normalize() also
            # drops the corresponding bracket from the prescribed text.
            return ""
        if "new end date" in inner:
            return FILLERS["extended_date"]
        if "amount" in inner or "$" in inner:
            return FILLERS["amount"]
        if "date" in inner:
            return FILLERS["date"]
        if "address" in inner:
            return FILLERS["address"]
        return FILLERS["name"]

    return re.sub(r"\[[^\]]*\]", repl, text).replace("*", "")


_SYNTHETIC_BODY = {
    "Rent": (
        f"Rent is {FILLERS['amount']} per week, payable in advance, with the "
        f"first payment due on {FILLERS['date']}."
    ),
}


def render_term(term) -> str:
    heading = _straighten(term.heading)
    body = _straighten(_fill(term.body))
    if not body.strip():
        # A rendered body left empty after filling is real table content the
        # corpus extraction cannot capture (VIC Form 1/2 term 6 "Rent" is a
        # table with no explanatory prose - the design spec's documented
        # "table-content limitation"). screen_terms gates on FormTerm.body
        # from the corpus, not this rendered text, so this substitution
        # cannot change what screens green - it only gives the judge
        # something concrete to read on the ~9 of every 10 documents where
        # this term is present and NOT the seeded missing/altered one.
        # Confirmed empirically: a bare "6. Rent" heading with nothing under
        # it made the judge correctly, but uselessly, report every such
        # document as missing rent particulars - precision 0.21-0.23 against
        # a rule that was never actually the seeded target.
        body = _SYNTHETIC_BODY.get(heading, f"{heading} as set out in this agreement.")
    return f"{term.section_no.rsplit('-T', 1)[1]}. {heading}\n{body}"


def build_verbatim(terms) -> str:
    parts = ["RESIDENTIAL TENANCY AGREEMENT", "The parties agree as follows."]
    parts += [render_term(t) for t in terms]
    return "\n\n".join(parts)


def build_missing(terms, missing: set[str]) -> str:
    return build_verbatim([t for t in terms if t.rule_id not in missing])


def build_altered(terms, alterations: dict[str, str]) -> str:
    parts = ["RESIDENTIAL TENANCY AGREEMENT", "The parties agree as follows."]
    for t in terms:
        if t.rule_id in alterations:
            no = t.section_no.rsplit("-T", 1)[1]
            heading = _straighten(t.heading)
            altered = _straighten(alterations[t.rule_id])
            parts.append(f"{no}. {heading}\n{altered}")
        else:
            parts.append(render_term(t))
    return "\n\n".join(parts)


def propose_alteration(body: str) -> str | None:
    """Authoring aid: crude adversarial edits - review before shipping."""
    for pattern, repl in [
        (r"\b(\d+) days\b", "24 hours"),
        (r"\bmust not\b", "may"),
        (r"\bthe landlord agrees\b", "the landlord may choose"),
        (r"\bat least\b", "at most"),
    ]:
        altered, n = re.subn(pattern, repl, body, count=1, flags=re.IGNORECASE)
        if n:
            return altered
    return None


ALTERATIONS = {
    "nsw.clause.sf_t1": (
        "The landlord may choose that the tenant has the right to occupy the "
        "residential premises during the tenancy. The residential premises include "
        "the additional things (if any) noted under “Residential premises”."
    ),
    "nsw.clause.sf_t2": (
        "The landlord may choose to give the tenant—2.1 a copy of this agreement "
        "before or when the tenant gives the signed copy of the agreement to the "
        "landlord or landlord’s agent, and2.2 a copy of this agreement signed by both "
        "the landlord and the tenant as soon as is reasonably practicable."
    ),
    "nsw.clause.sf_t3": (
        "The tenant must pay the full fixed-term rent in a single lump sum in cash "
        "before moving in, and must reimburse the landlord for any bank or dishonour "
        "fee the landlord incurs regardless of whose account was at fault."
    ),
    "nsw.clause.sf_t4": (
        "The landlord may require the tenant to pay up to six months rent in advance, "
        "may charge an administration fee for accepting payment by electronic "
        "transfer or Centrepay, and is not required to give the tenant a rent receipt "
        "or a record of rent paid."
    ),
    "nsw.clause.sf_t5": (
        "The landlord and the tenant agree that the rent cannot be increased unless "
        "the landlord gives not less than 24 hours written notice of the increase to "
        "the tenant. The notice must specify the increased rent and the day from "
        "which it is payable."
    ),
    "nsw.clause.sf_t6": (
        "The landlord and the tenant agree that the rent may be increased as often as "
        "the landlord considers necessary, without regard to how recently it was last "
        "increased."
    ),
    "nsw.clause.sf_t7": (
        "The landlord and the tenant agree that an increased rent takes effect "
        "immediately on the landlord giving verbal notice, whether or not the "
        "increase complies with the Residential Tenancies Act 2010 or has been "
        "approved by the Civil and Administrative Tribunal."
    ),
    "nsw.clause.sf_t8": (
        "The landlord and the tenant agree that the rent continues in full even if "
        "the residential premises are destroyed, become wholly uninhabitable, or are "
        "compulsorily acquired by an authority."
    ),
    "nsw.clause.sf_t9": (
        "The rent cannot be reduced at any time during this agreement, even if the "
        "landlord and the tenant both wish to agree to a reduction."
    ),
    "nsw.clause.sf_t10": (
        "The tenant agrees to pay all council rates, land tax, and the installation "
        "costs and charges for connecting electricity, water and gas to the premises, "
        "in addition to rent."
    ),
    "nsw.clause.sf_t11": (
        "The tenant agrees to pay all charges for electricity, gas and water supplied "
        "to the premises, whether or not the premises are separately metered, and all "
        "garbage charges without any excess-usage limit."
    ),
    "nsw.clause.sf_t12": (
        "The tenant must pay water usage charges on demand within 48 hours of the "
        "landlord's request, whether or not the landlord provides a copy of the water "
        "authority's bill or evidence of the amount used, and whether or not the "
        "premises meet any water efficiency standard."
    ),
    "nsw.clause.sf_t13": (
        "The landlord may choose to give the tenant the benefit of, or an amount "
        "equivalent to, any rebate received by the landlord for water usage charges "
        "payable or paid by the tenant."
    ),
    "nsw.clause.sf_t14": (
        "The landlord is not required to ensure the residential premises are vacant "
        "on the date the tenant is entitled to move in, and gives no assurance that "
        "there is no legal reason preventing the premises being used as a residence."
    ),
    "nsw.clause.sf_t15": (
        "The landlord and the landlord's agent may enter and use the residential "
        "premises at any time for any purpose, and the tenant has no right to object "
        "or to quiet enjoyment of the premises."
    ),
    "nsw.clause.sf_t16": (
        "The tenant may use the residential premises for any purpose, including "
        "activities that interfere with neighbours or cause damage, without "
        "restriction."
    ),
    "nsw.clause.sf_t17": (
        "The tenant is not responsible for keeping the premises clean, need not "
        "notify the landlord of damage, and the landlord must replace all light "
        "globes at the landlord's own cost."
    ),
    "nsw.clause.sf_t18": (
        "The tenant may leave the premises in whatever condition they are in at the "
        "end of the tenancy, need not remove rubbish or the tenant's own goods, and "
        "may keep any keys or opening devices provided by the landlord."
    ),
    "nsw.clause.sf_t19": (
        "The landlord is not required to keep the residential premises clean, fit to "
        "live in, or in a reasonable state of repair, and may interfere with the "
        "supply of gas, electricity or water to the premises at any time for any "
        "reason."
    ),
    "nsw.clause.sf_t20": (
        "The tenant may only recover the cost of urgent repairs if the landlord has "
        "given prior written approval for the repair; without that approval the "
        "landlord is not required to reimburse the tenant any amount."
    ),
    "nsw.clause.sf_t21": (
        "The landlord agrees—21.1 to give the tenant written notice that the landlord "
        "intends to sell the residential premises, at least 24 hours before the "
        "premises are made available for inspection by potential purchasers, and21.2 "
        "to make all reasonable efforts to agree with the tenant as to the days and "
        "times when the residential premises are to be available for inspection by "
        "potential purchasers."
    ),
    "nsw.clause.sf_t22": (
        "The tenant must agree to any request for inspection by potential purchasers, "
        "however frequent or inconvenient, and may not refuse even where the request "
        "is unreasonable."
    ),
    "nsw.clause.sf_t23": (
        "The landlord may show the residential premises to potential purchasers as "
        "often as the landlord chooses and need only give the tenant 2 hours notice "
        "each time."
    ),
    "nsw.clause.sf_t24": (
        "The landlord, the landlord's agent, or any person the landlord authorises "
        "may enter the residential premises at any time and for any purpose without "
        "giving the tenant any notice."
    ),
    "nsw.clause.sf_t25": (
        "A person entering under this agreement may enter at any hour of the day or "
        "night, including Sundays and public holidays, and need not notify the tenant "
        "of the time of entry."
    ),
    "nsw.clause.sf_t26": (
        "A person other than the landlord or the landlord's agent need not produce "
        "any written permission to enter the residential premises, even outside an "
        "emergency."
    ),
    "nsw.clause.sf_t27": (
        "The tenant is not required to give access to the residential premises even "
        "where the landlord or the landlord's agent is exercising a right to enter in "
        "accordance with this agreement."
    ),
    "nsw.clause.sf_t28": (
        "The landlord or the landlord's agent may publish photographs or visual "
        "recordings of the inside of the residential premises in which the tenant's "
        "possessions are visible without first obtaining the tenant's written "
        "consent."
    ),
    "nsw.clause.sf_t29": (
        "The landlord or the landlord's agent may publish photographs or visual "
        "recordings showing the tenant's possessions without the tenant's consent, "
        "including where the tenant is in circumstances of domestic violence."
    ),
    "nsw.clause.sf_t30": (
        "The tenant may install fixtures, or renovate, alter or add to the "
        "residential premises, without the landlord's permission, and the landlord "
        "may require the tenant to pay for repairing any resulting damage regardless "
        "of who caused it."
    ),
    "nsw.clause.sf_t31": (
        "The landlord may unreasonably withhold consent to any fixture, or to an "
        "alteration, addition or renovation of a minor nature, without giving any "
        "reason."
    ),
    "nsw.clause.sf_t32": (
        "The landlord is not required to provide or maintain locks or security "
        "devices for the residential premises, and may charge the tenant for every "
        "copy of a key regardless of cause."
    ),
    "nsw.clause.sf_t33": (
        "The tenant may alter, remove or add any lock or security device at any time "
        "without the landlord's agreement, and is not required to ever give the "
        "landlord a copy of the key."
    ),
    "nsw.clause.sf_t34": (
        "A copy of a changed key or opening device must always be given to the other "
        "party even where the Civil and Administrative Tribunal has authorised "
        "withholding it or an apprehended violence order prohibits that party's "
        "access to the premises."
    ),
    "nsw.clause.sf_t35": (
        "The landlord may unreasonably refuse permission to transfer or sub-let any "
        "part of the tenancy, without needing a reason, even for a partial transfer."
    ),
    "nsw.clause.sf_t36": (
        "The landlord may choose not to charge for giving permission other than for "
        "the landlord’s reasonable expenses in giving permission."
    ),
    "nsw.clause.sf_t37": (
        "The landlord is not required to notify the tenant if the landlord's contact "
        "details, address, or agent change at any time during the tenancy."
    ),
    "nsw.clause.sf_t38": (
        "The landlord may choose to give to the tenant, before the tenant enters into "
        "this agreement, a copy of the by-laws applying to the residential premises "
        "if they are premises under the Strata Schemes Management Act 2015."
    ),
    "nsw.clause.sf_t39": (
        "The landlord is not required to give the tenant a copy of the by-laws "
        "applying to the residential premises, even where the premises are under the "
        "Strata Schemes Development Act 2015, the Community Land Development Act 2021 "
        "or the Community Land Management Act 2021."
    ),
    "nsw.clause.sf_t40": (
        "The rules of law relating to mitigation of loss do not apply to a breach of "
        "this agreement, and the landlord may claim the full amount of any loss "
        "regardless of whether it could have been avoided by reasonable effort."
    ),
    "nsw.clause.sf_t41": (
        "The landlord or the landlord's agent is not required to provide the tenant "
        "with details of the amount claimed, supporting receipts, or a copy of the "
        "condition report when applying for payment of the rental bond."
    ),
    "nsw.clause.sf_t42": (
        "The landlord is not required to check, maintain, or repair smoke alarms on "
        "the residential premises, and any repair may take as long as the landlord "
        "chooses."
    ),
    "nsw.clause.sf_t43": (
        "The tenant must personally repair or replace any faulty smoke alarm at the "
        "tenant's own cost, including hardwired alarms, without notifying the "
        "landlord."
    ),
    "nsw.clause.sf_t44": (
        "The landlord and the tenant may remove or disable a smoke alarm installed on "
        "the residential premises for any reason, without needing a reasonable "
        "excuse."
    ),
    "nsw.clause.sf_t45": (
        "The landlord may choose to ensure that the requirements of the Swimming "
        "Pools Act 1992 have been complied with in respect of the swimming pool on "
        "the residential premises."
    ),
    "nsw.clause.sf_t46": (
        "The landlord is not required to register the swimming pool or to obtain or "
        "provide the tenant with a valid certificate of compliance under the Swimming "
        "Pools Act 1992."
    ),
    "nsw.clause.sf_t47": (
        "The landlord is not required to tell the tenant if the residential premises "
        "are or become listed on the LFAI Register for loose-fill asbestos "
        "insulation."
    ),
    "nsw.clause.sf_t48": (
        "The landlord is not required to tell the tenant if the residential premises "
        "become subject to a fire safety order or building product rectification "
        "order relating to combustible cladding."
    ),
    "nsw.clause.sf_t49": (
        "The landlord is not required to tell the tenant if the premises become "
        "subject to a significant health or safety risk during the tenancy."
    ),
    "nsw.clause.sf_t50": (
        "Once the tenant consents to electronic service of notices, the tenant may "
        "never withdraw that consent for the remainder of the tenancy, and the "
        "landlord need not notify the tenant of a change to the service email "
        "address."
    ),
    "nsw.clause.sf_t51": (
        "The tenant agrees that, if the tenant ends the residential tenancy agreement "
        "before the end of the fixed term, the tenant must pay a break fee equal to "
        "the whole of the rent remaining for the rest of the fixed term, regardless "
        "of how much of the term has expired."
    ),
    "nsw.clause.sf_t52": (
        "The compensation payable by the tenant for ending the residential tenancy "
        "agreement early is not limited to the amount specified in clause 51, and the "
        "landlord may claim additional amounts at the landlord's discretion."
    ),
    "nsw.clause.sf_t53": (
        "The landlord may refuse an application to keep an animal at the premises "
        "without giving any reason, and may impose any condition on consent, whether "
        "or not it is reasonable."
    ),
    "nsw.clause.sf_t54": (
        "If the landlord does not respond to a pet application within 21 days, "
        "consent is refused rather than deemed given, and the landlord may refuse "
        "consent to keep an animal for any reason at all."
    ),
    "nsw.clause.sf_t55": (
        "The landlord and the tenant agree that either party may end this agreement "
        "at any time for any reason, without following the Residential Tenancies Act "
        "2010 or the Residential Tenancies Regulation 2019."
    ),
    "nsw.clause.sf_t56": (
        "The landlord may choose whether the tenant is allowed to keep an animal at "
        "the residential premises."
    ),
    "nsw.clause.sf_t57": (
        "The tenant agrees to have the carpet professionally cleaned, or to pay for "
        "professional cleaning, at the end of the tenancy regardless of whether the "
        "animal caused this to be required."
    ),
    "nsw.clause.sf_t58": (
        "The tenant agrees to have the premises professionally fumigated, or to pay "
        "the cost of fumigation, at the end of the tenancy regardless of whether the "
        "animal caused this to be required."
    ),
    "nsw.clause.sf_t59": (
        "The tenant is not required to take any steps to prevent the animal being "
        "inside the premises, even where the animal is a type not normally kept "
        "inside."
    ),
    "vic.clause.sf_f1_t1": (
        "The date of this agreement is the date the first party signs, even where the "
        "parties sign on different days and the other party has not yet "
        "countersigned."
    ),
    "vic.clause.sf_f1_t2": (
        "Address of premises: to be confirmed by the rental provider before the "
        "tenancy starts, and the rental provider may substitute a different property "
        "of the rental provider's choosing."
    ),
    "vic.clause.sf_f1_t3": (
        "The rental provider need not notify the renter if the rental provider's or "
        "the agent's name, address, phone number or email address changes during the "
        "tenancy."
    ),
    "vic.clause.sf_f1_t4": (
        "Only one renter's name needs to be recorded on this agreement, even where "
        "more than one person will occupy the premises as a renter."
    ),
    "vic.clause.sf_f1_t5": (
        "Note: If a fixed term agreement ends and the renter and rental provider do "
        "not enter into a new fixed term agreement, the renter must vacate the "
        "premises immediately and no periodic tenancy will be formed."
    ),
    "vic.clause.sf_f1_t6": (
        "Rent is $550.00 per week, payable three months in advance in a single lump "
        "sum, non-refundable if the tenancy ends early."
    ),
    "vic.clause.sf_f1_t7": (
        "The rental provider may require a bond of up to three months' rent "
        "regardless of the weekly rent amount, and is not required to lodge the bond "
        "with the Residential Tenancies Bond Authority."
    ),
    "vic.clause.sf_f1_t8": (
        "The rental provider may require the renter to pay rent only by a payment "
        "method that charges the renter a fee, and need not allow payment by "
        "Centrepay or electronic funds transfer."
    ),
    "vic.clause.sf_f1_t9": (
        "Once the rental provider ticks yes to electronic service, notices may be "
        "served on the renter by email even where the renter has not separately "
        "agreed to electronic service."
    ),
    "vic.clause.sf_f1_t10": (
        "The rental provider is not required to maintain the rental property in good "
        "repair, and need not provide the renter with any emergency contact details "
        "for urgent repairs."
    ),
    "vic.clause.sf_f1_t11": (
        "The renter must arrange and pay for professional cleaning of the rented "
        "premises at the end of the tenancy in every case, regardless of the "
        "condition of the premises at the start of the tenancy."
    ),
    "vic.clause.sf_f1_t12": (
        "If owners corporation rules apply to the premises, the rental provider is "
        "not required to attach a copy of the rules to this agreement."
    ),
    "vic.clause.sf_f1_t13": (
        "The condition report will be given to the renter within 3 months after the "
        "renter moves into the rented premises."
    ),
    "vic.clause.sf_f1_t14": (
        "The rental provider is not required to conduct any electrical safety check "
        "of the rented premises, and need not provide the renter with the date of the "
        "most recent check."
    ),
    "vic.clause.sf_f1_t15": (
        "The rental provider is not required to conduct any gas safety check of the "
        "rented premises, even where the premises contain gas appliances or fittings."
    ),
    "vic.clause.sf_f1_t17": (
        "The rental provider is not required to maintain the swimming pool barrier in "
        "good repair or to repair it urgently once notified that it is not in working "
        "order."
    ),
    "vic.clause.sf_f1_t18": (
        "The renter may erect a relocatable swimming pool of any depth on the rented "
        "premises without giving the rental provider written notice or obtaining any "
        "approval."
    ),
    "vic.clause.sf_f1_t19": (
        "The rental provider is not required to maintain the bushfire water tank or "
        "its connected infrastructure in good repair, even where the premises are in "
        "a designated bushfire prone area."
    ),
    "vic.clause.sf_f1_t20": (
        "The renter may use the premises for an illegal purpose, and is not required "
        "to notify the rental provider in writing where the renter causes damage to "
        "the premises or common areas."
    ),
    "vic.clause.sf_f1_t21": (
        "The rental provider is not required to ensure the premises meet the rental "
        "minimum standards or are reasonably clean when the renter moves in."
    ),
    "vic.clause.sf_f1_t22": (
        "The renter must seek the rental provider's written consent before making any "
        "modification to the premises, including modifications listed on the Consumer "
        "Affairs Victoria website as not requiring consent."
    ),
    "vic.clause.sf_f1_t23": (
        "The rental provider may give a key to the premises to a person who is "
        "excluded from the premises under a family violence intervention order, a "
        "family violence safety notice, or a personal safety intervention order."
    ),
    "vic.clause.sf_f1_t24": (
        "Repairs, both urgent and non-urgent, may be carried out by any person the "
        "rental provider considers suitable, whether or not that person is suitably "
        "qualified."
    ),
    "vic.clause.sf_f1_t25": (
        "The renter must always arrange and pay for urgent repairs personally, and "
        "the rental provider is not required to reimburse the renter any amount, even "
        "where the rental provider fails to carry out the repairs after being "
        "notified."
    ),
    "vic.clause.sf_f1_t26": (
        "If the rental provider does not carry out non-urgent repairs, the renter has "
        "no right to apply to VCAT for an order requiring the rental provider to do "
        "the repairs, however long the repairs are delayed."
    ),
    "vic.clause.sf_f1_t27": (
        "The rental provider may charge the renter a fee of the rental provider's "
        "choosing for consenting to an assignment or sub-letting, beyond the rental "
        "provider's reasonable expenses."
    ),
    "vic.clause.sf_f1_t28": (
        "The rental provider must give the renter at least 24 hours written notice of "
        "a proposed rent increase. The rent cannot be increased more than once every "
        "12 months. The rental provider must not increase the rent under a fixed term "
        "agreement unless the agreement provides for an increase by specifying the "
        "amount of increase or the method of calculating the rent increase."
    ),
    "vic.clause.sf_f1_t29": (
        "The rental provider may enter the premises at any time without notice for a "
        "routine inspection, and the renter is not entitled to any compensation for "
        "sales inspections."
    ),
    "vic.clause.sf_f1_t30": (
        "The renter must seek consent from the rental provider before keeping a pet "
        "on the premises. The rental provider may unreasonably refuse a request to "
        "keep a pet."
    ),
    "vic.clause.sf_f1_t30a": (
        "The rental provider is not required to ensure smoke alarms in the rented "
        "premises are installed, working, or tested, and any renter request for "
        "urgent repair of a faulty smoke alarm may be refused."
    ),
    "vic.clause.sf_f1_t31": (
        "Additional terms in this agreement may exclude, restrict or modify any of "
        "the rights and duties included in the Act, and need not comply with the "
        "Australian Consumer Law (Victoria)."
    ),
    "vic.clause.sf_f1_t32": (
        "Only one renter needs to sign this agreement, even where more than one "
        "renter is a party to it, and the rental provider need not provide Part D "
        "(Rights and Obligations) before signing."
    ),
    "vic.clause.sf_f2_t1": (
        "The date of this agreement is the date the first party signs, even where the "
        "parties sign on different days and the other party has not yet "
        "countersigned."
    ),
    "vic.clause.sf_f2_t2": (
        "Address of premises: to be confirmed by the rental provider before the "
        "tenancy starts, and the rental provider may substitute a different property "
        "of the rental provider's choosing."
    ),
    "vic.clause.sf_f2_t3": (
        "The rental provider need not notify the renter if the rental provider's or "
        "the agent's name, address, phone number or email address changes during the "
        "tenancy."
    ),
    "vic.clause.sf_f2_t4": (
        "Only one renter's name needs to be recorded on this agreement, even where "
        "more than one person will occupy the premises as a renter."
    ),
    "vic.clause.sf_f2_t5": (
        "There is no minimum term for this agreement; the end date may be any period "
        "after the start date, even less than 5 years."
    ),
    "vic.clause.sf_f2_t6": (
        "Rent is $550.00 per week, payable three months in advance in a single lump "
        "sum, non-refundable if the tenancy ends early."
    ),
    "vic.clause.sf_f2_t7": (
        "The rental provider may require a bond of up to three months' rent "
        "regardless of the weekly rent amount, and is not required to lodge the bond "
        "with the Residential Tenancies Bond Authority."
    ),
    "vic.clause.sf_f2_t8": (
        "The rental provider may require an additional amount of bond at any time "
        "during the agreement, without giving the 120 days notice, and may do so more "
        "than once within any 5 year period."
    ),
    "vic.clause.sf_f2_t9": (
        "The rental provider need only permit one payment method for rent, and is not "
        "required to allow the renter to use Centrepay or any other form of "
        "electronic funds transfer."
    ),
    "vic.clause.sf_f2_t10": (
        "Once the rental provider ticks yes to electronic service, notices may be "
        "served on the renter by email even where the renter has not separately "
        "agreed to electronic service."
    ),
    "vic.clause.sf_f2_t11": (
        "The rental provider is not required to maintain the rented premises in good "
        "repair, and need not provide the renter with any emergency contact details "
        "for urgent repairs such as a burst water service or gas leak."
    ),
    "vic.clause.sf_f2_t12": (
        "The renter must arrange and pay for professional cleaning of the rented "
        "premises at the end of the tenancy in every case, regardless of the "
        "condition of the premises at the start of the tenancy."
    ),
    "vic.clause.sf_f2_t13": (
        "If owners corporation rules apply to the premises, the rental provider is "
        "not required to attach a copy of the rules to this agreement."
    ),
    "vic.clause.sf_f2_t14": (
        "The condition report will be given to the renter within 3 months after the "
        "renter moves into the rented premises."
    ),
    "vic.clause.sf_f2_t15": (
        "The rental provider may increase the rent by any amount at any time during "
        "the agreement, without following any of the CPI, Statewide Rent Index, or "
        "fixed-percentage methods, and without giving 90 days written notice."
    ),
    "vic.clause.sf_f2_t16": (
        "If the renter ends the agreement early, the renter must pay the rental "
        "provider the whole of the rent remaining for the rest of the fixed term, "
        "regardless of the rental provider's actual losses or any duty to re-let the "
        "premises."
    ),
    "vic.clause.sf_f2_t17": (
        "The rental provider may refuse to extend this agreement on any terms other "
        "than a substantial rent increase, and the renter has no ability to negotiate "
        "the extension terms."
    ),
    "vic.clause.sf_f2_t18": (
        "The renter must restore the premises and pay the full cost of restoration "
        "for any alteration, addition, installation or renovation, even where the "
        "rental provider agreed in writing that no restoration would be required."
    ),
    "vic.clause.sf_f2_t19": (
        "The rental provider is not required to conduct any electrical safety check "
        "of the rented premises, and need not provide the renter with the date of the "
        "most recent check."
    ),
    "vic.clause.sf_f2_t20": (
        "The rental provider is not required to conduct any gas safety check of the "
        "rented premises, even where the premises contain gas appliances or fittings."
    ),
    "vic.clause.sf_f2_t22": (
        "The rental provider is not required to maintain the swimming pool barrier in "
        "good repair or to repair it urgently once notified that it is not in working "
        "order."
    ),
    "vic.clause.sf_f2_t23": (
        "The renter may erect a relocatable swimming pool of any size without giving "
        "the rental provider written notice or obtaining any approval."
    ),
    "vic.clause.sf_f2_t24": (
        "The rental provider is not required to maintain the bushfire water tank or "
        "its connected infrastructure in good repair, even where the premises are in "
        "a designated bushfire prone area."
    ),
    "vic.clause.sf_f2_t25": (
        "The renter may use the premises for an illegal purpose, and is not required "
        "to notify the rental provider in writing where the renter causes damage to "
        "the premises or common areas."
    ),
    "vic.clause.sf_f2_t26": (
        "The rental provider is not required to ensure the premises meet the rental "
        "minimum standards or are reasonably clean when the renter moves in."
    ),
    "vic.clause.sf_f2_t27": (
        "The renter must seek the rental provider's written consent before making any "
        "modification to the premises, including modifications listed on the Consumer "
        "Affairs Victoria website as not requiring consent."
    ),
    "vic.clause.sf_f2_t28": (
        "The rental provider may give a key to the premises to a person who is "
        "excluded from the premises under a family violence intervention order, a "
        "family violence safety notice, or a personal safety intervention order."
    ),
    "vic.clause.sf_f2_t29": (
        "Repairs, both urgent and non-urgent, may be carried out by any person the "
        "rental provider considers suitable, whether or not that person is suitably "
        "qualified."
    ),
    "vic.clause.sf_f2_t30": (
        "The renter must always arrange and pay for urgent repairs personally, and "
        "the rental provider is not required to reimburse the renter any amount, even "
        "where the rental provider fails to carry out the repairs after being "
        "notified."
    ),
    "vic.clause.sf_f2_t31": (
        "If the rental provider does not carry out non-urgent repairs, the renter has "
        "no right to apply to VCAT for an order requiring the rental provider to do "
        "the repairs, however long the repairs are delayed."
    ),
    "vic.clause.sf_f2_t32": (
        "The rental provider may charge the renter a fee of the rental provider's "
        "choosing for consenting to an assignment or sub-letting, beyond the rental "
        "provider's reasonable expenses."
    ),
    "vic.clause.sf_f2_t33": (
        "The rental provider must give the renter at least 24 hours written notice of "
        "a proposed rent increase. The rent cannot be increased more than once every "
        "12 months. The rental provider must not increase the rent unless the "
        "agreement provides for an increase by specifying the amount of increase or "
        "the method of calculating the rent increase."
    ),
    "vic.clause.sf_f2_t34": (
        "The rental provider may enter the premises at any time without notice for a "
        "routine inspection, and the renter is not entitled to any compensation for "
        "sales inspections."
    ),
    "vic.clause.sf_f2_t35": (
        "The renter must seek consent from the rental provider before keeping a pet "
        "on the premises. The rental provider may unreasonably refuse a request to "
        "keep a pet."
    ),
    "vic.clause.sf_f2_t35a": (
        "The rental provider is not required to ensure smoke alarms in the rented "
        "premises are installed, working, or tested, and any renter request for "
        "urgent repair of a faulty smoke alarm may be refused."
    ),
    "vic.clause.sf_f2_t36": (
        "Additional terms in this agreement may exclude, restrict or modify any of "
        "the rights and duties included in the Act, and need not comply with the "
        "Australian Consumer Law (Victoria)."
    ),
    "vic.clause.sf_f2_t37": (
        "Only one renter needs to sign this agreement, even where more than one "
        "renter is a party to it, and the rental provider need not provide Part D "
        "(Rights and Obligations) before signing."
    ),
    "vic.clause.sf_f2_t38": (
        "Where the rental provider and the renter agree to extend this agreement, the "
        "new end date may be for any period, even less than 5 years from the "
        "commencement date, and only the rental provider needs to sign the extension."
    ),
    "vic.clause.sf_f2_t39": (
        "The rental provider's consent to the alterations, additions, installations "
        "or renovations specified in this Part may be withdrawn by the rental "
        "provider at any time, even after the renter has completed the work."
    ),
    "vic.clause.sf_f2_t40": (
        "The renter must pay the full estimated cost of restoring the premises at the "
        "end of the tenancy even where the rental provider ticked the box agreeing "
        "that no restoration or payment would be required."
    ),
}

PARAPHRASES = {
    "nsw.clause.sf_t3": (
        "The tenant will pay the rent when it falls due, will cover the landlord's "
        "bank charges if the tenant's payment fails to clear, and agrees the way rent "
        "is paid can only be changed if both landlord and tenant consent."
    ),
    "nsw.clause.sf_t15": (
        "The landlord promises the tenant may use and enjoy the residential premises "
        "undisturbed by the landlord, anyone claiming through the landlord, or anyone "
        "with a superior title such as a head landlord, and that neither the landlord "
        "nor the landlord's agent will interfere with the tenant's reasonable peace, "
        "comfort or privacy, or allow neighbouring tenants under the landlord's "
        "control to do so."
    ),
    "nsw.clause.sf_t16": (
        "The tenant will not use the premises for any unlawful purpose, will not "
        "create a nuisance, will not disturb the reasonable peace, comfort or privacy "
        "of neighbours, will avoid deliberately or carelessly damaging the premises, "
        "and will not let more people live there than this agreement allows."
    ),
    "nsw.clause.sf_t19": (
        "The landlord will keep the residential premises reasonably clean and fit to "
        "live in as required by section 52 of the Residential Tenancies Act 2010, "
        "will make sure light fittings have working globes when the tenancy starts, "
        "will keep the premises in a reasonable state of repair given its age and "
        "rent, and will not cut off gas, electricity, water or other services except "
        "where necessary for safety or repairs. The landlord will not stop a "
        "tradesperson entering to carry out urgent health or safety repairs, will "
        "meet every statutory obligation about the health or safety of the premises, "
        "and will not hold a domestic violence victim, or an innocent co-tenant under "
        "the same agreement, responsible for damage a co-tenant caused while "
        "committing a domestic violence offence."
    ),
    "nsw.clause.sf_t32": (
        "The landlord will supply and maintain the locks and security devices needed "
        "to keep the premises reasonably secure, will give each tenant a copy of the "
        "keys they are entitled to, will not charge for the first copies, will not "
        "change the locks without a reasonable excuse or the tenant's agreement, and "
        "will provide new keys within 7 days of any change."
    ),
    "vic.clause.sf_f1_t1": (
        "The agreement takes effect on the date it is signed; where the parties sign "
        "on different dates, the agreement is dated from whichever signature is "
        "later."
    ),
    "vic.clause.sf_f1_t2": "Address of premises: 1 Example Street, Sydney NSW 2000.",
    "vic.clause.sf_f1_t3": (
        "This section records the rental provider's full name, address, phone number, "
        "ACN and email, or those of the rental provider's agent if one acts for them, "
        "and the rental provider must tell the renter within 7 days if any of these "
        "details change."
    ),
    "vic.clause.sf_f1_t4": (
        "Every renter who is a party to this agreement lists their full name, current "
        "address, phone number and email address in this section."
    ),
    "vic.clause.sf_f1_t5": (
        "Where a fixed-term agreement expires and the renter stays on without the "
        "parties signing a new fixed-term agreement, the tenancy automatically "
        "continues as a periodic agreement, for example month to month."
    ),
    "vic.clause.sf_f1_t6": (
        "Rent is $550.00 per week, payable in advance in accordance with the payment "
        "method set out in this agreement."
    ),
}

SF_THRESHOLDS = {"precision": 0.9, "recall": 0.8}


@dataclass(frozen=True)
class PlannedDocument:
    doc_id: str
    text: str
    expected: dict[str, str]  # rule_id -> "red" | "green"


_CHUNK_SIZE = 10


def _sibling_clusters(terms: list) -> list[list]:
    """Consecutive runs of terms sharing one heading, as atomic chunk units.

    Sibling terms (e.g. NSW's two "SWIMMING POOLS" terms, S1-T45/T46) must
    move together into the same missing/altered document: seeding one
    absent or altered while its sibling sits untouched nearby lets the
    judge credit the untouched sibling's text against the manipulated term
    - confirmed by a diagnostic re-run where the model's "covered" verdict
    on an altered T45 cited T46's independent, still-verbatim obligation as
    substantively satisfying T45. Chunking below packs whole clusters, never
    splitting one across a chunk boundary.
    """
    clusters: list[list] = []
    for term in terms:
        if clusters and clusters[-1][0].heading == term.heading:
            clusters[-1].append(term)
        else:
            clusters.append([term])
    return _merge_related(clusters)


_RELATED_GROUPS = [
    {"vic.clause.sf_f2_t17", "vic.clause.sf_f2_t38"},
    {"vic.clause.sf_f2_t18", "vic.clause.sf_f2_t39", "vic.clause.sf_f2_t40"},
    {"vic.clause.sf_f2_t33", "vic.clause.sf_f2_t15"},
]
"""Same-form-different-heading term families confirmed by diagnostic re-run
to cross-match the same way as an identical-heading sibling pair. VIC Form
2's Part F schedule terms restate, in schedule/form shape, the substantive
right their own earlier term establishes - "Extension of agreement length"
(T17) / "Extension of term" (T38); "Modifications" (T18) / "Agreed
modifications" (T39) / "Restoration requirements" (T40) - and "Rent" (T33,
the increase-notice clause) restates "Rent adjustments" (T15) in simpler
terms. None share a heading, so _sibling_clusters' adjacent-heading pass
never merges them, but leaving one missing/altered while its relative sits
untouched nearby let the judge credit the untouched relative's text against
the seeded one on 6 of 6 diagnostic re-runs for T18 alone. VIC Form 1 has no
equivalent split (its Modifications and Rent-increase terms are
self-contained, with no separate Part F schedule restating them), so this
list is empty for every other jurisdiction/form - _merge_related is then a
no-op, confirmed by NSW and VIC F1 both passing unaffected."""


def _merge_related(clusters: list[list]) -> list[list]:
    for group in _RELATED_GROUPS:
        idxs = [i for i, c in enumerate(clusters) if any(t.rule_id in group for t in c)]
        if len(idxs) <= 1:
            continue
        merged = [t for i in idxs for t in clusters[i]]
        for i in sorted(idxs, reverse=True):
            del clusters[i]
        clusters.append(merged)
    return clusters


def _cluster_chunks(clusters: list[list], size: int, start: int) -> list[list]:
    """Clusters, rotated to start at cluster index `start`, packed greedily
    into chunks of at most `size` terms apiece; no cluster is split.

    The rotation is a full permutation of `clusters`, so every term lands in
    exactly one chunk per call - two calls with different `start` values put
    every term through two different chunk neighbourhoods, one per pass.
    """
    order = clusters[start:] + clusters[:start]
    chunks: list[list] = []
    current: list = []
    for cluster in order:
        if current and len(current) + len(cluster) > size:
            chunks.append(current)
            current = []
        current.extend(cluster)
    if current:
        chunks.append(current)
    return chunks


def plan_documents(terms: list) -> list[PlannedDocument]:
    """Verbatim x2, a three-pass missing matrix, a three-pass altered matrix,
    and one paraphrase document (when PARAPHRASES covers any of `terms`).

    The missing and altered matrices each pack `terms` into ~10-rule_id
    sibling-respecting chunks three times, each pass starting a third of the
    way further through the cluster order, so every term is seeded missing
    (or altered) in exactly 3 documents - one per pass (n=6 recall cases per
    term total, satisfying the design spec's "at least 2" floor with slack
    to spare: at n=6 the recall gate tolerates exactly one noisy miss,
    5/6 = 0.833 >= 0.8, where n=4 required a perfect 4/4 - eval evidence
    showed per-case judgment noise alone was enough to flip individual
    rules between runs at n=4, with no content or wording defect to fix).
    Every document's expected label for every rule_id follows directly from
    how it was built: a missing or altered rule_id is red, everything else
    present verbatim (or faithfully paraphrased) is green.
    """
    all_ids = [t.rule_id for t in terms]
    docs = [
        PlannedDocument(f"verbatim-{i + 1}", build_verbatim(terms), dict.fromkeys(all_ids, "green"))
        for i in range(2)
    ]

    clusters = _sibling_clusters(terms)
    c = len(clusters)
    starts = (0, c // 3, 2 * c // 3)
    for pass_no, start in enumerate(starts):
        for chunk_no, chunk_terms in enumerate(_cluster_chunks(clusters, _CHUNK_SIZE, start)):
            missing_ids = {t.rule_id for t in chunk_terms}
            expected = {rid: ("red" if rid in missing_ids else "green") for rid in all_ids}
            docs.append(
                PlannedDocument(
                    f"missing-{pass_no}-{chunk_no}", build_missing(terms, missing_ids), expected
                )
            )

    for pass_no, start in enumerate(starts):
        for chunk_no, chunk_terms in enumerate(_cluster_chunks(clusters, _CHUNK_SIZE, start)):
            chunk_ids = {t.rule_id for t in chunk_terms if t.rule_id in ALTERATIONS}
            subset = {rid: ALTERATIONS[rid] for rid in chunk_ids}
            expected = {rid: ("red" if rid in chunk_ids else "green") for rid in all_ids}
            docs.append(
                PlannedDocument(
                    f"altered-{pass_no}-{chunk_no}", build_altered(terms, subset), expected
                )
            )

    paraphrased_ids = {t.rule_id for t in terms if t.rule_id in PARAPHRASES}
    if paraphrased_ids:
        subset = {rid: PARAPHRASES[rid] for rid in paraphrased_ids}
        docs.append(
            PlannedDocument(
                "paraphrase", build_altered(terms, subset), dict.fromkeys(all_ids, "green")
            )
        )

    return docs
