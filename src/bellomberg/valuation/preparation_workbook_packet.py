"""Persist the exact bindings already produced by the common workbook builders."""
from copy import deepcopy


def capture_bindings(workbook, payload):
    """No numeric calculation or inferred cell address is introduced here."""
    link = getattr(workbook, '_model_link', None)
    if not isinstance(link, dict):
        return None
    if payload.get('method') == 'managed_care_distributable_equity':
        # This builder historically retained annual rows only. Preserve its
        # exact engine subtotals too, as the other intrinsic builders do.
        sheet = workbook.create_sheet('Engine Baseline')
        sheet.append(['Engine path', 'Recorded value'])
        def flatten(value, prefix=''):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from flatten(child, prefix + '.' + str(key) if prefix else str(key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from flatten(child, prefix + '.' + str(index))
            else:
                yield prefix, value
        for scenario, values in payload.get('managed_care', {}).get('scenarios', {}).items():
            for key, value in flatten(values, scenario):
                sheet.append([key, value])
        sheet.column_dimensions['A'].width = 55
        sheet.freeze_panes = 'B2'
    packet = {'contract': 'common_workbook_bindings/1',
        'input_refs': [{'scenario': key[0], 'driver': key[1], 'path': list(key[2:]), 'cell': value}
                       for key, value in link.get('refs', {}).items()],
        **{key: deepcopy(link.get(key, {})) for key in ('raw', 'shares', 'guards', 'calls')}}
    payload.setdefault('calculation_details', {})['workbook_bindings'] = packet
    return packet
