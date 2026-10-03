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
# PM 03/10/2026: the Capo writes like the weekly Capo (MEDIUM effort, same 128000
# output room) so the memo is never cut by the token limit. Frozen literal: a later
# change of the weekly CAPO_MAX_TOKENS must not silently rewrite this contract.
EXECUTION_POLICY_V3 = 'trade-idea-research/3'
ROLE_EFFORT_V3 = MappingProxyType({
    'specialist': 'medium', 'red_team': 'medium', 'capo': 'medium', 'aux': 'medium'})
CAPO_OUTPUT_V3 = 128000
_POLICIES = MappingProxyType({
    EXECUTION_POLICY_V2: (ROLE_EFFORT_V2, CAPO_OUTPUT_V2),
    EXECUTION_POLICY_V3: (ROLE_EFFORT_V3, CAPO_OUTPUT_V3)})
RESEARCH_POLICIES = frozenset(_POLICIES)
CURRENT_EXECUTION_POLICY = EXECUTION_POLICY_V3


def execution_policy(value):
    get = value.get if isinstance(value, dict) else lambda name, default=None: getattr(value, name, default)
    policy = get('execution_policy')
    if policy is None:
        return None
    if policy not in RESEARCH_POLICIES or get('analysis_mode') != RESEARCH_ANALYSIS_MODE:
        raise ValueError('Unknown or incompatible Trade Idea execution policy')
    return policy


def role_effort(value, role):
    if role not in ROLE_EFFORT_V2:
        raise ValueError('Unknown Trade Idea role: ' + str(role))
    policy = execution_policy(value)
    return _POLICIES[policy][0][role] if policy is not None else 'max'


def role_thinking(value, role):
    return {'type': 'effort', 'effort': role_effort(value, role)}


def report_quality_sufficient(quality):
    """One qualification rule per contract.

    The V2 memo has no page or word floor: a ready PDF whose original text is
    integrally present qualifies. The historical contract keeps its 10 pages /
    4000 analytical words.
    """
    if not isinstance(quality, dict) or quality.get('status') != 'ready':
        return False
    if quality.get('execution_policy') in RESEARCH_POLICIES:
        return quality.get('content_integrity') == 'complete'
    return quality.get('analytical_pages', 0) >= 10 and quality.get('analytical_words', 0) >= 4000


def output_cap(value, role, legacy_cap):
    policy = execution_policy(value)
    return _POLICIES[policy][1] if policy is not None and role == 'capo' else legacy_cap
