"""Contract identity and Russian count labels shared by execution views."""

def counted(number, one, few, many):
    form = many if 11 <= number % 100 <= 14 else one if number % 10 == 1 else few if 2 <= number % 10 <= 4 else many
    return f"{number} {form}"


def contract_key(tender, spec):
    contract = " ".join(spec["contract"].split()).casefold()
    if not contract:
        return ("missing", tender.id)
    return ("contract", " ".join(tender.customer.split()).casefold(), contract)
