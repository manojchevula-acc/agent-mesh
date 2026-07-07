"""Ticket-size and tenor eligibility checks. Technical Spec §7.3."""


def ticket_eligible(requested_amount: float, min_ticket_size: float,
                    max_ticket_size: float) -> bool:
    """True iff min_ticket_size <= requested_amount <= max_ticket_size."""
    return min_ticket_size <= requested_amount <= max_ticket_size


def tenor_eligible(tenor: str, eligible_tenors_csv: str) -> bool:
    """eligible_tenors_csv is comma-delimited, e.g. '1M,3M,6M,12M'."""
    allowed = {t.strip() for t in eligible_tenors_csv.split(",")}
    return tenor in allowed


def segment_eligible(segment: str, eligible_segments_csv: str) -> bool:
    """eligible_segments_csv is comma-delimited, e.g. 'Corporate,SME'."""
    allowed = {s.strip() for s in eligible_segments_csv.split(",")}
    return segment in allowed
