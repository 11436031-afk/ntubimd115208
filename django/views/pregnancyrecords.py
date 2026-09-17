from datetime import timedelta
from core.models import PregnancyRecord, PregnancyCase


def records_for_case(pregnancy_case):
    if not pregnancy_case or not pregnancy_case.user_id:
        return PregnancyRecord.objects.none()

    user_id = pregnancy_case.user_id
    cases = list(
        PregnancyCase.objects.filter(user_id=user_id)
        .order_by('menstruation', 'pregnancycase_id')
    )
    if len(cases) <= 1:
        qs = PregnancyRecord.objects.filter(user_id=user_id)
        if pregnancy_case.menstruation:
            qs = qs.filter(check_date__gte=pregnancy_case.menstruation)
        return qs

    try:
        idx = [c.pregnancycase_id for c in cases].index(pregnancy_case.pregnancycase_id)
    except ValueError:
        return PregnancyRecord.objects.filter(user_id=user_id)

    case = cases[idx]
    start_date = case.menstruation

    born_babies = (
        case.babyinformation_set
        .filter(birthdaytime__isnull=False)
        .order_by('-birthdaytime')
    )
    if born_babies.exists():
        birth_date = born_babies.first().birthdaytime.date()
        if idx + 1 < len(cases):
            next_case = cases[idx + 1]
            end_date = min(birth_date + timedelta(days=60), next_case.menstruation - timedelta(days=1))
        else:
            end_date = birth_date + timedelta(days=60)
    else:
        if idx + 1 < len(cases):
            next_case = cases[idx + 1]
            end_date = next_case.menstruation - timedelta(days=1)
        else:
            end_date = None

    qs = PregnancyRecord.objects.filter(user_id=user_id)
    if start_date:
        qs = qs.filter(check_date__gte=start_date)
    if end_date:
        qs = qs.filter(check_date__lte=end_date)
    return qs