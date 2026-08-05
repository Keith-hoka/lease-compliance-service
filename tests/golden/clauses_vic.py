"""Seeded VIC golden set for the LLM clause audit.

Scoring contract (identical to the NSW set): every case's target rule
expects case.expected and every other rule expects green - each case is
a hard negative for the other fifteen rules. yellow on a red case is a
recall miss; red on a green case is a precision hit against the judging
rule.

Terminology: mostly post-2021 "residential rental provider"/"renter";
cases suffixed "-oldstyle" deliberately use "landlord"/"tenant" - real
VIC templates blend both and recall must hold on either.
"""

from tests.golden.clauses import ClauseCase

_PREAMBLE = "RESIDENTIAL RENTAL AGREEMENT between rental provider and renter. "

VIC_PROHIBITED_CASES = [
    # --- vic.clause.renter_insurance ---
    ClauseCase(
        "vic-insurance-red-plain",
        "vic.clause.renter_insurance",
        _PREAMBLE + "The renter must take out and maintain contents insurance "
        "for the duration of the tenancy.",
        "red",
    ),
    ClauseCase(
        "vic-insurance-red-specified",
        "vic.clause.renter_insurance",
        _PREAMBLE + "The renter shall obtain public liability insurance of no "
        "less than $10 million from an insurer approved by the rental "
        "provider and provide a certificate of currency on request.",
        "red",
    ),
    ClauseCase(
        "vic-insurance-red-oldstyle",
        "vic.clause.renter_insurance",
        "RESIDENTIAL TENANCY AGREEMENT. The tenant agrees to effect an "
        "insurance policy covering the tenant's possessions and any glass "
        "breakage at the property for the term of the lease.",
        "red",
    ),
    # --- vic.clause.fixed_break_fees ---
    ClauseCase(
        "vic-breakfee-red-flat",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "If the renter gives notice of intention to vacate and "
        "ends the agreement before the end of the fixed term, a lease "
        "break fee of $1,500 applies.",
        "red",
    ),
    ClauseCase(
        "vic-breakfee-red-schedule",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "Where the renter ends the agreement early by giving "
        "the required notice, a fixed administration charge of two weeks "
        "rent plus a $250 advertising levy applies, payable on vacating.",
        "red",
    ),
    ClauseCase(
        "vic-breakfee-red-paraphrase",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "Should the renter give notice and vacate prior to "
        "the expiry date, a set reletting amount of $990 becomes due.",
        "red",
    ),
    ClauseCase(
        "vic-breakfee-green-basis",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "If the renter terminates early, a reletting fee applies "
        "calculated as: the rental provider's actual advertising costs plus "
        "a pro rata portion of the letting fee, being one week's rent "
        "multiplied by the fraction of the fixed term remaining.",
        "green",
    ),
    # --- hard greens for the other carve-out rules ---
    ClauseCase(
        "vic-cleaningreq-green-27c-shape",
        "vic.clause.professional_cleaning_required",
        _PREAMBLE + "The premises must be professionally cleaned at the end "
        "of the tenancy only if professional cleaning becomes required to "
        "restore the premises to the condition they were in immediately "
        "before the start of the tenancy, taking into account fair wear "
        "and tear.",
        "green",
    ),
    ClauseCase(
        "vic-cleaningcost-green-27c-shape",
        "vic.clause.professional_cleaning_cost",
        _PREAMBLE + "The renter must pay the cost of professional cleaning "
        "only where such cleaning becomes required to restore the premises "
        "to their condition immediately before the start of the tenancy, "
        "fair wear and tear excepted.",
        "green",
    ),
    ClauseCase(
        "vic-thirdparty-green-embedded",
        "vic.clause.third_party_services",
        _PREAMBLE + "Electricity to the premises is supplied through the "
        "building's embedded network operated by OnPower Pty Ltd and the "
        "renter must acquire electricity from that embedded network "
        "supplier.",
        "green",
    ),
    ClauseCase(
        "vic-payment-green-bankfees",
        "vic.clause.costly_payment_method",
        _PREAMBLE + "Rent is payable by direct debit from the renter's "
        "nominated bank account; any bank or account fees charged by the "
        "renter's own financial institution are the renter's "
        "responsibility.",
        "green",
    ),
    ClauseCase(
        "vic-breach-green-lawful-breakfee",
        "vic.clause.breach_penalty",
        _PREAMBLE + "If the renter ends the agreement early, the renter is "
        "liable for the reasonable costs of reletting, calculated as the "
        "advertising actually incurred plus a letting fee prorated to the "
        "unexpired portion of the term.",
        "green",
    ),
    # --- vic.clause.provider_liability_exemption ---
    ClauseCase(
        "vic-liabexempt-red-plain",
        "vic.clause.provider_liability_exemption",
        _PREAMBLE + "The rental provider will not be liable for any loss, "
        "injury or damage suffered by the renter or the renter's visitors "
        "at the premises, however caused.",
        "red",
    ),
    ClauseCase(
        "vic-liabexempt-red-agent",
        "vic.clause.provider_liability_exemption",
        _PREAMBLE + "The rental provider will not be liable for any act "
        "or omission of the rental provider's agent, or of any person "
        "acting on the agent's behalf, including damage to the renter's "
        "belongings caused by that person's negligence.",
        "red",
    ),
    ClauseCase(
        "vic-liabexempt-red-paraphrase",
        "vic.clause.provider_liability_exemption",
        _PREAMBLE + "Should the renter suffer any injury, loss or damage "
        "connected with the premises, responsibility rests with the renter "
        "alone; neither the owner of the premises nor anyone managing the "
        "property on the owner's behalf accepts any responsibility.",
        "red",
    ),
    # --- vic.clause.breach_penalty ---
    ClauseCase(
        "vic-breach-red-remaining-rent",
        "vic.clause.breach_penalty",
        _PREAMBLE + "If the renter breaches any term of this agreement, the "
        "renter must pay the rental provider the whole of the rent "
        "remaining for the balance of the fixed term.",
        "red",
    ),
    ClauseCase(
        "vic-breach-red-penalty-damages",
        "vic.clause.breach_penalty",
        _PREAMBLE + "Any contravention of this agreement by the renter "
        "renders the renter liable for liquidated damages of $2,000 in "
        "addition to a penalty equal to two weeks rent.",
        "red",
    ),
    ClauseCase(
        "vic-breach-red-increased-rent",
        "vic.clause.breach_penalty",
        _PREAMBLE + "Where the renter fails to comply with an obligation "
        "under this agreement, the weekly rent payable for the remainder "
        "of the term increases to $650 as a consequence of the "
        "non-compliance.",
        "red",
    ),
    # --- vic.clause.professional_cleaning_required ---
    ClauseCase(
        "vic-cleaningreq-red-plain",
        "vic.clause.professional_cleaning_required",
        _PREAMBLE + "The premises must be professionally cleaned throughout, "
        "including all carpets and window furnishings, at the end of the "
        "tenancy.",
        "red",
    ),
    ClauseCase(
        "vic-cleaningreq-red-irrespective-condition",
        "vic.clause.professional_cleaning_required",
        _PREAMBLE + "The premises must be professionally cleaned at the end "
        "of the tenancy irrespective of the condition the premises were in "
        "when the tenancy began.",
        "red",
    ),
    ClauseCase(
        "vic-cleaningreq-red-paraphrase",
        "vic.clause.professional_cleaning_required",
        _PREAMBLE + "A qualified cleaning contractor must attend the "
        "property and carry out a full clean before the keys are handed "
        "back, regardless of how clean the premises already are.",
        "red",
    ),
    # --- vic.clause.professional_cleaning_cost ---
    ClauseCase(
        "vic-cleaningcost-red-plain",
        "vic.clause.professional_cleaning_cost",
        _PREAMBLE + "The cost of professionally cleaning the premises at "
        "the end of the tenancy must be paid by the renter.",
        "red",
    ),
    ClauseCase(
        "vic-cleaningcost-red-bond-deduction",
        "vic.clause.professional_cleaning_cost",
        _PREAMBLE + "The cost of professional cleaning carried out at "
        "the end of the tenancy will be deducted from the renter's bond.",
        "red",
    ),
    ClauseCase(
        "vic-cleaningcost-red-paraphrase",
        "vic.clause.professional_cleaning_cost",
        _PREAMBLE + "Reimbursement for professionally cleaning the property "
        "after the renter moves out is to be made by the renter to the "
        "rental provider within 7 days of receiving the invoice.",
        "red",
    ),
    # --- vic.clause.no_breach_rent_inducement ---
    ClauseCase(
        "vic-inducement-red-plain",
        "vic.clause.no_breach_rent_inducement",
        _PREAMBLE + "Provided the renter does not breach any term of this "
        "agreement during the fixed term, the weekly rent will be reduced "
        "by $15 from the beginning of the final month.",
        "red",
    ),
    ClauseCase(
        "vic-inducement-red-bonus",
        "vic.clause.no_breach_rent_inducement",
        _PREAMBLE + "If the renter complies with all conditions of this "
        "agreement throughout the tenancy, the renter may be paid a bonus "
        "of $200 at the end of the term.",
        "red",
    ),
    ClauseCase(
        "vic-inducement-red-paraphrase",
        "vic.clause.no_breach_rent_inducement",
        _PREAMBLE + "Renters who keep to every condition of this lease for "
        "the full twelve months will see their final month's rent cut by "
        "half.",
        "red",
    ),
    # --- vic.clause.preparation_costs ---
    ClauseCase(
        "vic-prepcosts-red-plain",
        "vic.clause.preparation_costs",
        _PREAMBLE + "The renter must pay the rental provider's costs of "
        "preparing this residential rental agreement, including any fees "
        "charged by the provider's agent.",
        "red",
    ),
    ClauseCase(
        "vic-prepcosts-red-oldstyle",
        "vic.clause.preparation_costs",
        "RESIDENTIAL TENANCY AGREEMENT. Each party agrees to bear the "
        "other party's legal costs incurred in the preparation of this "
        "lease, including the landlord's solicitor's fees for drawing up "
        "the agreement.",
        "red",
    ),
    ClauseCase(
        "vic-prepcosts-red-paraphrase",
        "vic.clause.preparation_costs",
        _PREAMBLE + "Before signing, the renter reimburses the agent's "
        "document-drawing fee for putting this lease together.",
        "red",
    ),
    # --- vic.clause.unreviewed_contract ---
    ClauseCase(
        "vic-unreviewed-red-plain",
        "vic.clause.unreviewed_contract",
        _PREAMBLE + "The renter agrees to be bound by the owners "
        "corporation's shared-services agreement with the building "
        "operator, as amended from time to time, without being provided "
        "a copy before entering this agreement.",
        "red",
    ),
    ClauseCase(
        "vic-unreviewed-red-service-contract",
        "vic.clause.unreviewed_contract",
        _PREAMBLE + "The renter is bound by the terms of the building's "
        "car parking licence agreement as in force from time to time, a "
        "copy of which need not be provided before signing.",
        "red",
    ),
    ClauseCase(
        "vic-unreviewed-red-paraphrase",
        "vic.clause.unreviewed_contract",
        _PREAMBLE + "The renter is taken to have accepted whatever terms "
        "the owners corporation has agreed under its building management "
        "contract from time to time, even though a copy of that contract "
        "has not been shown to the renter.",
        "red",
    ),
    # --- vic.clause.renter_indemnity ---
    ClauseCase(
        "vic-indemnity-red-plain",
        "vic.clause.renter_indemnity",
        _PREAMBLE + "The renter indemnifies the rental provider against "
        "any loss, claim or liability arising in connection with the "
        "renter's occupation of the premises.",
        "red",
    ),
    ClauseCase(
        "vic-indemnity-red-broad",
        "vic.clause.renter_indemnity",
        _PREAMBLE + "The renter must indemnify and keep indemnified the "
        "rental provider and the rental provider's agent from and against "
        "all actions, claims and costs of whatsoever nature arising out of "
        "the renter's use or occupation of the premises.",
        "red",
    ),
    ClauseCase(
        "vic-indemnity-red-paraphrase",
        "vic.clause.renter_indemnity",
        _PREAMBLE + "The renter agrees to cover and make good any expense "
        "the rental provider incurs, and to compensate the rental provider "
        "in full, for any claim made by a third party connected with the "
        "tenancy.",
        "red",
    ),
    # --- vic.clause.late_availability_claim_waiver ---
    ClauseCase(
        "vic-lateavail-red-plain",
        "vic.clause.late_availability_claim_waiver",
        _PREAMBLE + "The renter is not entitled to make any claim for "
        "compensation against the rental provider if the premises are not "
        "available for occupation on the start date of this agreement.",
        "red",
    ),
    ClauseCase(
        "vic-lateavail-red-oldstyle",
        "vic.clause.late_availability_claim_waiver",
        "RESIDENTIAL TENANCY AGREEMENT. Should the property not be ready "
        "for the tenant to move in on the agreed start date, the tenant "
        "waives any right to compensation from the landlord for the "
        "delay.",
        "red",
    ),
    ClauseCase(
        "vic-lateavail-red-paraphrase",
        "vic.clause.late_availability_claim_waiver",
        _PREAMBLE + "If, for any reason, the renter cannot take possession "
        "on the day the tenancy is due to begin, the renter accepts that "
        "no payment or credit will be owed by the rental provider.",
        "red",
    ),
    # --- vic.clause.costly_payment_method ---
    ClauseCase(
        "vic-payment-red-card-surcharge",
        "vic.clause.costly_payment_method",
        _PREAMBLE + "Rent must be paid in advance by credit card through "
        "the rental provider's online portal, which applies a 1.5% card "
        "surcharge on every payment.",
        "red",
    ),
    ClauseCase(
        "vic-payment-red-app-fee",
        "vic.clause.costly_payment_method",
        _PREAMBLE + "The renter must pay rent in advance using the rental "
        "provider's own payment app, which charges the renter a $2.95 "
        "service fee on each transaction.",
        "red",
    ),
    ClauseCase(
        "vic-payment-red-paraphrase",
        "vic.clause.costly_payment_method",
        _PREAMBLE + "Rent must be paid one month in advance by bank "
        "cheque or money order, obtained at the renter's own cost, "
        "delivered to the rental provider's office.",
        "red",
    ),
    # --- vic.clause.third_party_services ---
    ClauseCase(
        "vic-thirdparty-red-security",
        "vic.clause.third_party_services",
        _PREAMBLE + "The renter must use SecureGuard Monitoring Pty Ltd, "
        "as nominated by the rental provider, for any home security "
        "monitoring service at the premises.",
        "red",
    ),
    ClauseCase(
        "vic-thirdparty-red-garden",
        "vic.clause.third_party_services",
        _PREAMBLE + "Lawn mowing and garden maintenance at the premises "
        "must be carried out exclusively by TidyYards Pty Ltd, the "
        "contractor nominated by the rental provider, at the renter's "
        "cost.",
        "red",
    ),
    ClauseCase(
        "vic-thirdparty-red-paraphrase",
        "vic.clause.third_party_services",
        _PREAMBLE + "Whenever the internet connection at the property "
        "needs setting up, the renter is required to sign up with WaveNet "
        "Broadband, the provider the rental provider has selected for the "
        "building.",
        "red",
    ),
    # --- vic.clause.safety_maintenance_transfer ---
    ClauseCase(
        "vic-safety-red-oldstyle",
        "vic.clause.safety_maintenance_transfer",
        "RESIDENTIAL TENANCY AGREEMENT. The tenant is responsible for "
        "arranging and paying for the annual smoke alarm safety check at "
        "the premises.",
        "red",
    ),
    ClauseCase(
        "vic-safety-red-gascheck",
        "vic.clause.safety_maintenance_transfer",
        _PREAMBLE + "Any fee charged for the annual gas appliance safety "
        "inspection is payable by the renter.",
        "red",
    ),
    ClauseCase(
        "vic-safety-red-paraphrase",
        "vic.clause.safety_maintenance_transfer",
        _PREAMBLE + "Keeping the property's fire-detection equipment in "
        "working order, including engaging a technician and covering "
        "whatever that technician charges, falls to the renter.",
        "red",
    ),
    # --- vic.clause.tribunal_costs_transfer ---
    ClauseCase(
        "vic-tribunal-red-plain",
        "vic.clause.tribunal_costs_transfer",
        _PREAMBLE + "If the rental provider applies to the Victorian Civil "
        "and Administrative Tribunal in relation to this tenancy, the "
        "renter must pay the rental provider's application fee.",
        "red",
    ),
    ClauseCase(
        "vic-tribunal-red-broad",
        "vic.clause.tribunal_costs_transfer",
        _PREAMBLE + "The renter is liable for all costs incurred by the "
        "rental provider in making any application to the Tribunal about "
        "this tenancy, including the filing fee and any other charges the "
        "Tribunal imposes.",
        "red",
    ),
    ClauseCase(
        "vic-tribunal-red-paraphrase",
        "vic.clause.tribunal_costs_transfer",
        _PREAMBLE + "Should the rental provider need to lodge a case with "
        "VCAT about this tenancy, whatever it costs the rental provider to "
        "lodge that case is recoverable from the renter.",
        "red",
    ),
    # --- vic.clause.insurance_excess_transfer ---
    ClauseCase(
        "vic-excess-red-plain",
        "vic.clause.insurance_excess_transfer",
        _PREAMBLE + "The renter is liable to pay the excess under the "
        "rental provider's landlord insurance policy for any claim made "
        "in connection with the premises.",
        "red",
    ),
    ClauseCase(
        "vic-excess-red-bond-deduction",
        "vic.clause.insurance_excess_transfer",
        _PREAMBLE + "Any insurance excess payable under the rental "
        "provider's building insurance policy will automatically be "
        "deducted from the renter's bond.",
        "red",
    ),
    ClauseCase(
        "vic-excess-red-paraphrase",
        "vic.clause.insurance_excess_transfer",
        _PREAMBLE + "Where the rental provider makes a claim on their own "
        "property insurance relating to the premises, the renter covers "
        "whatever contribution amount the insurer requires before the "
        "insurer pays out.",
        "red",
    ),
]
