"""Immutable execution choices for newly accepted company-research runs.

Absence is the historical contract, never an invitation to upgrade an old run.
Changing these choices requires a new policy version.
"""
from types import MappingProxyType

from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE

EXECUTION_POLICY_V2 = 'trade-idea-research/2'
ROLE_EFFORT_V2 = MappingProxyType({
    'specialist': 'medium', 'red_team': 'medium', 'capo': 'low', 'aux': 'medium'})
CAPO_OUTPUT_V2 = 32768


def execution_policy(value):
    get = value.get if isinstance(value, dict) else lambda name, default=None: getattr(value, name, default)
    policy = get('execution_policy')
    if policy is None:
        return None
    if policy != EXECUTION_POLICY_V2 or get('analysis_mode') != RESEARCH_ANALYSIS_MODE:
        raise ValueError('Unknown or incompatible Trade Idea execution policy')
    return policy


def role_effort(value, role):
    if role not in ROLE_EFFORT_V2:
        raise ValueError('Unknown Trade Idea role: ' + str(role))
    return ROLE_EFFORT_V2[role] if execution_policy(value) == EXECUTION_POLICY_V2 else 'max'


def role_thinking(value, role):
    return {'type': 'effort', 'effort': role_effort(value, role)}


def output_cap(value, role, legacy_cap):
    return CAPO_OUTPUT_V2 if execution_policy(value) == EXECUTION_POLICY_V2 and role == 'capo' else legacy_cap
