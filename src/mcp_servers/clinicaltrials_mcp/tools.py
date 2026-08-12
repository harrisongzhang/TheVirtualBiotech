"""
Clinical Data MCP Tools
Tool implementations for:
1. ClinicalTrials.gov API v2 - Clinical trial information
2. cBioPortal API - Cancer genomics and clinical data
"""

import requests
import time
import logging
from typing import Optional, Dict, Any, List
import pandas as pd

# cBioPortal imports
from pybioportal import (
    cancer_types,
    studies,
    molecular_profiles,
    mutations,
    molecular_data,
    clinical_data as cbio_clinical,
    clinical_attributes,
    samples,
    discrete_copy_number_alterations,
    genes
)

logger = logging.getLogger(__name__)

# API configuration
BASE_URL = "https://clinicaltrials.gov/api/v2"
RATE_LIMIT_DELAY = 1.2  # Seconds between requests (50/min = 1.2s/request with buffer)
REQUEST_TIMEOUT = 30  # Seconds


# Rate limiting decorator
class RateLimiter:
    """Simple rate limiter for API calls"""
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.last_call = 0

    def wait(self):
        """Wait if necessary to respect rate limit"""
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()


_rate_limiter = RateLimiter(RATE_LIMIT_DELAY)


def _safe_get(data: dict, *keys, default=None):
    """Safely get nested dictionary value"""
    result = data
    for key in keys:
        if isinstance(result, dict):
            result = result.get(key, default)
        else:
            return default
    return result


def _extract_protocol_section(protocol: dict) -> dict:
    """Extract key information from protocolSection"""

    # Identification
    ident = protocol.get('identificationModule', {})

    # Status
    status = protocol.get('statusModule', {})

    # Design
    design = protocol.get('designModule', {})

    # Description
    desc = protocol.get('descriptionModule', {})

    # Conditions
    conditions = protocol.get('conditionsModule', {})

    # Interventions
    interventions_mod = protocol.get('armsInterventionsModule', {})

    # Outcomes
    outcomes = protocol.get('outcomesModule', {})

    # Eligibility
    eligibility = protocol.get('eligibilityModule', {})

    # Sponsor
    sponsor = protocol.get('sponsorCollaboratorsModule', {})

    # Locations
    locations_mod = protocol.get('contactsLocationsModule', {})

    # Oversight
    oversight = protocol.get('oversightModule', {})

    return {
        # === IDENTIFICATION ===
        'nctId': ident.get('nctId'),
        'orgStudyId': _safe_get(ident, 'orgStudyIdInfo', 'id'),
        'secondaryIds': [
            {
                'id': sid.get('id'),
                'type': sid.get('type'),
                'link': sid.get('link')
            } for sid in ident.get('secondaryIdInfos', [])
        ],
        'briefTitle': ident.get('briefTitle'),
        'officialTitle': ident.get('officialTitle'),
        'acronym': ident.get('acronym'),

        # === STATUS ===
        'overallStatus': status.get('overallStatus'),
        'statusVerifiedDate': status.get('statusVerifiedDate'),
        'lastKnownStatus': status.get('lastKnownStatus'),
        'whyStopped': status.get('whyStopped'),
        'startDate': _safe_get(status, 'startDateStruct', 'date'),
        'startDateType': _safe_get(status, 'startDateStruct', 'type'),
        'primaryCompletionDate': _safe_get(status, 'primaryCompletionDateStruct', 'date'),
        'primaryCompletionDateType': _safe_get(status, 'primaryCompletionDateStruct', 'type'),
        'completionDate': _safe_get(status, 'completionDateStruct', 'date'),
        'completionDateType': _safe_get(status, 'completionDateStruct', 'type'),
        'studyFirstSubmitDate': status.get('studyFirstSubmitDate'),
        'studyFirstPostDate': _safe_get(status, 'studyFirstPostDateStruct', 'date'),
        'lastUpdateSubmitDate': status.get('lastUpdateSubmitDate'),
        'lastUpdatePostDate': _safe_get(status, 'lastUpdatePostDateStruct', 'date'),
        'expandedAccess': _safe_get(status, 'expandedAccessInfo', 'hasExpandedAccess'),

        # === DESIGN ===
        'studyType': design.get('studyType'),
        'phases': design.get('phases', []),
        'allocation': _safe_get(design, 'designInfo', 'allocation'),
        'interventionModel': _safe_get(design, 'designInfo', 'interventionModel'),
        'interventionModelDescription': _safe_get(design, 'designInfo', 'interventionModelDescription'),
        'primaryPurpose': _safe_get(design, 'designInfo', 'primaryPurpose'),
        'masking': _safe_get(design, 'designInfo', 'maskingInfo', 'masking'),
        'maskingDescription': _safe_get(design, 'designInfo', 'maskingInfo', 'maskingDescription'),
        'whoMasked': _safe_get(design, 'designInfo', 'maskingInfo', 'whoMasked', default=[]),
        'enrollmentCount': _safe_get(design, 'enrollmentInfo', 'count'),
        'enrollmentType': _safe_get(design, 'enrollmentInfo', 'type'),

        # === DESCRIPTION ===
        'briefSummary': desc.get('briefSummary'),
        'detailedDescription': desc.get('detailedDescription'),

        # === CONDITIONS ===
        'conditions': conditions.get('conditions', []),
        'keywords': conditions.get('keywords', []),

        # === INTERVENTIONS ===
        'interventions': [
            {
                'type': interv.get('type'),
                'name': interv.get('name'),
                'description': interv.get('description'),
                'armGroupLabels': interv.get('armGroupLabels', []),
                'otherNames': interv.get('otherNames', [])
            } for interv in interventions_mod.get('interventions', [])
        ],
        'armGroups': [
            {
                'label': arm.get('label'),
                'type': arm.get('type'),
                'description': arm.get('description'),
                'interventionNames': arm.get('interventionNames', [])
            } for arm in interventions_mod.get('armGroups', [])
        ],

        # === OUTCOMES ===
        'primaryOutcomes': [
            {
                'measure': outcome.get('measure'),
                'description': outcome.get('description'),
                'timeFrame': outcome.get('timeFrame')
            } for outcome in outcomes.get('primaryOutcomes', [])
        ],
        'secondaryOutcomes': [
            {
                'measure': outcome.get('measure'),
                'description': outcome.get('description'),
                'timeFrame': outcome.get('timeFrame')
            } for outcome in outcomes.get('secondaryOutcomes', [])
        ],

        # === ELIGIBILITY ===
        'eligibilityCriteria': eligibility.get('eligibilityCriteria'),
        'healthyVolunteers': eligibility.get('healthyVolunteers'),
        'sex': eligibility.get('sex'),
        'minimumAge': eligibility.get('minimumAge'),
        'maximumAge': eligibility.get('maximumAge'),
        'stdAges': eligibility.get('stdAges', []),

        # === SPONSOR & COLLABORATORS ===
        'leadSponsor': _safe_get(sponsor, 'leadSponsor', 'name'),
        'leadSponsorClass': _safe_get(sponsor, 'leadSponsor', 'class'),
        'collaborators': [
            {
                'name': collab.get('name'),
                'class': collab.get('class')
            } for collab in sponsor.get('collaborators', [])
        ],
        'responsibleParty': {
            'type': _safe_get(sponsor, 'responsibleParty', 'type'),
            'investigator': _safe_get(sponsor, 'responsibleParty', 'investigatorFullName'),
            'affiliation': _safe_get(sponsor, 'responsibleParty', 'investigatorAffiliation')
        } if 'responsibleParty' in sponsor else None,

        # === LOCATIONS ===
        'locations': [
            {
                'facility': loc.get('facility'),
                'status': loc.get('status'),
                'city': loc.get('city'),
                'state': loc.get('state'),
                'zip': loc.get('zip'),
                'country': loc.get('country'),
                'geoPoint': loc.get('geoPoint')
            } for loc in locations_mod.get('locations', [])
        ],

        # === OVERSIGHT ===
        'oversightHasDmc': oversight.get('oversightHasDmc'),
        'isFdaRegulatedDrug': oversight.get('isFdaRegulatedDrug'),
        'isFdaRegulatedDevice': oversight.get('isFdaRegulatedDevice'),
        'isUnapprovedDevice': oversight.get('isUnapprovedDevice'),

        # === IPD SHARING ===
        'ipdSharing': _safe_get(protocol, 'ipdSharingStatementModule', 'ipdSharing'),
    }


def _extract_derived_section(derived: dict) -> dict:
    """Extract key information from derivedSection"""

    condition_browse = derived.get('conditionBrowseModule', {})
    intervention_browse = derived.get('interventionBrowseModule', {})

    return {
        'versionDate': _safe_get(derived, 'miscInfoModule', 'versionHolder'),
        'conditionMeshTerms': [
            {'id': mesh.get('id'), 'term': mesh.get('term')}
            for mesh in condition_browse.get('meshes', [])
        ],
        'interventionMeshTerms': [
            {'id': mesh.get('id'), 'term': mesh.get('term')}
            for mesh in intervention_browse.get('meshes', [])
        ]
    }


def _extract_results_section(results: dict) -> dict:
    """Extract key information from resultsSection"""

    if not results:
        return {'hasResults': False}

    # Participant Flow Module
    flow_mod = results.get('participantFlowModule', {})

    # Baseline Characteristics Module
    baseline_mod = results.get('baselineCharacteristicsModule', {})

    # Outcome Measures Module
    outcomes_mod = results.get('outcomeMeasuresModule', {})

    # Adverse Events Module
    ae_mod = results.get('adverseEventsModule', {})

    # More Info Module
    more_info_mod = results.get('moreInfoModule', {})

    return {
        'hasResults': True,

        # === PARTICIPANT FLOW ===
        'participantFlow': {
            'preAssignmentDetails': flow_mod.get('preAssignmentDetails'),
            'recruitmentDetails': flow_mod.get('recruitmentDetails'),
            'groups': [
                {
                    'id': grp.get('id'),
                    'title': grp.get('title'),
                    'description': grp.get('description')
                } for grp in flow_mod.get('groups', [])
            ],
            'periods': [
                {
                    'title': period.get('title'),
                    'milestones': [
                        {
                            'type': milestone.get('type'),
                            'comment': milestone.get('comment'),
                            'achievements': [
                                {
                                    'groupId': ach.get('groupId'),
                                    'numSubjects': ach.get('numSubjects'),
                                    'comment': ach.get('comment')
                                } for ach in milestone.get('achievements', [])
                            ]
                        } for milestone in period.get('milestones', [])
                    ],
                    'dropWithdraws': [
                        {
                            'type': drop.get('type'),
                            'comment': drop.get('comment'),
                            'reasons': [
                                {
                                    'groupId': reason.get('groupId'),
                                    'numSubjects': reason.get('numSubjects'),
                                    'comment': reason.get('comment')
                                } for reason in drop.get('reasons', [])
                            ]
                        } for drop in period.get('dropWithdraws', [])
                    ]
                } for period in flow_mod.get('periods', [])
            ]
        } if flow_mod else None,

        # === BASELINE CHARACTERISTICS ===
        'baselineCharacteristics': {
            'populationDescription': baseline_mod.get('populationDescription'),
            'groups': [
                {
                    'id': grp.get('id'),
                    'title': grp.get('title'),
                    'description': grp.get('description')
                } for grp in baseline_mod.get('groups', [])
            ],
            'measures': [
                {
                    'title': measure.get('title'),
                    'description': measure.get('description'),
                    'populationDescription': measure.get('populationDescription'),
                    'paramType': measure.get('paramType'),
                    'dispersionType': measure.get('dispersionType'),
                    'unitOfMeasure': measure.get('unitOfMeasure'),
                    'classes': [
                        {
                            'title': cls.get('title'),
                            'categories': [
                                {
                                    'title': cat.get('title'),
                                    'measurements': [
                                        {
                                            'groupId': meas.get('groupId'),
                                            'value': meas.get('value'),
                                            'spread': meas.get('spread'),
                                            'lowerLimit': meas.get('lowerLimit'),
                                            'upperLimit': meas.get('upperLimit'),
                                            'comment': meas.get('comment')
                                        } for meas in cat.get('measurements', [])
                                    ]
                                } for cat in cls.get('categories', [])
                            ]
                        } for cls in measure.get('classes', [])
                    ]
                } for measure in baseline_mod.get('measures', [])
            ]
        } if baseline_mod else None,

        # === OUTCOME MEASURES (RESULTS) ===
        'outcomeMeasures': [
            {
                'type': outcome.get('type'),
                'title': outcome.get('title'),
                'description': outcome.get('description'),
                'populationDescription': outcome.get('populationDescription'),
                'reportingStatus': outcome.get('reportingStatus'),
                'anticipatedPostingDate': outcome.get('anticipatedPostingDate'),
                'paramType': outcome.get('paramType'),
                'dispersionType': outcome.get('dispersionType'),
                'unitOfMeasure': outcome.get('unitOfMeasure'),
                'timeFrame': outcome.get('timeFrame'),
                'groups': [
                    {
                        'id': grp.get('id'),
                        'title': grp.get('title'),
                        'description': grp.get('description')
                    } for grp in outcome.get('groups', [])
                ],
                'denoms': [
                    {
                        'units': denom.get('units'),
                        'counts': [
                            {
                                'groupId': count.get('groupId'),
                                'value': count.get('value')
                            } for count in denom.get('counts', [])
                        ]
                    } for denom in outcome.get('denoms', [])
                ],
                'classes': [
                    {
                        'title': cls.get('title'),
                        'categories': [
                            {
                                'title': cat.get('title'),
                                'measurements': [
                                    {
                                        'groupId': meas.get('groupId'),
                                        'value': meas.get('value'),
                                        'spread': meas.get('spread'),
                                        'lowerLimit': meas.get('lowerLimit'),
                                        'upperLimit': meas.get('upperLimit'),
                                        'comment': meas.get('comment')
                                    } for meas in cat.get('measurements', [])
                                ]
                            } for cat in cls.get('categories', [])
                        ]
                    } for cls in outcome.get('classes', [])
                ],
                'analyses': [
                    {
                        'groupIds': analysis.get('groupIds', []),
                        'groupDescription': analysis.get('groupDescription'),
                        'testedNonInferiority': analysis.get('testedNonInferiority'),
                        'nonInferiorityType': analysis.get('nonInferiorityType'),
                        'nonInferiorityComment': analysis.get('nonInferiorityComment'),
                        'pValue': analysis.get('pValue'),
                        'pValueComment': analysis.get('pValueComment'),
                        'statisticalMethod': analysis.get('statisticalMethod'),
                        'statisticalComment': analysis.get('statisticalComment'),
                        'paramType': analysis.get('paramType'),
                        'paramValue': analysis.get('paramValue'),
                        'ciPctValue': analysis.get('ciPctValue'),
                        'ciNumSides': analysis.get('ciNumSides'),
                        'ciLowerLimit': analysis.get('ciLowerLimit'),
                        'ciUpperLimit': analysis.get('ciUpperLimit'),
                        'ciUpperLimitComment': analysis.get('ciUpperLimitComment'),
                        'dispersionType': analysis.get('dispersionType'),
                        'dispersionValue': analysis.get('dispersionValue'),
                        'estimateComment': analysis.get('estimateComment'),
                        'otherAnalysisDescription': analysis.get('otherAnalysisDescription')
                    } for analysis in outcome.get('analyses', [])
                ]
            } for outcome in outcomes_mod.get('outcomeMeasures', [])
        ],

        # === ADVERSE EVENTS ===
        'adverseEvents': {
            'frequencyThreshold': ae_mod.get('frequencyThreshold'),
            'timeFrame': ae_mod.get('timeFrame'),
            'description': ae_mod.get('description'),
            'groups': [
                {
                    'id': grp.get('id'),
                    'title': grp.get('title'),
                    'description': grp.get('description'),
                    'deathsNumAffected': grp.get('deathsNumAffected'),
                    'deathsNumAtRisk': grp.get('deathsNumAtRisk'),
                    'seriousNumAffected': grp.get('seriousNumAffected'),
                    'seriousNumAtRisk': grp.get('seriousNumAtRisk'),
                    'otherNumAffected': grp.get('otherNumAffected'),
                    'otherNumAtRisk': grp.get('otherNumAtRisk')
                } for grp in ae_mod.get('groups', [])
            ],
            'seriousEvents': [
                {
                    'term': event.get('term'),
                    'organSystem': event.get('organSystem'),
                    'sourceVocabulary': event.get('sourceVocabulary'),
                    'assessmentType': event.get('assessmentType'),
                    'notes': event.get('notes'),
                    'stats': [
                        {
                            'groupId': stat.get('groupId'),
                            'numEvents': stat.get('numEvents'),
                            'numAffected': stat.get('numAffected'),
                            'numAtRisk': stat.get('numAtRisk')
                        } for stat in event.get('stats', [])
                    ]
                } for event in ae_mod.get('seriousEvents', [])
            ],
            'otherEvents': [
                {
                    'term': event.get('term'),
                    'organSystem': event.get('organSystem'),
                    'sourceVocabulary': event.get('sourceVocabulary'),
                    'assessmentType': event.get('assessmentType'),
                    'notes': event.get('notes'),
                    'stats': [
                        {
                            'groupId': stat.get('groupId'),
                            'numEvents': stat.get('numEvents'),
                            'numAffected': stat.get('numAffected'),
                            'numAtRisk': stat.get('numAtRisk')
                        } for stat in event.get('stats', [])
                    ]
                } for event in ae_mod.get('otherEvents', [])
            ]
        } if ae_mod else None,

        # === MORE INFO ===
        'moreInfo': {
            'certainAgreement': _safe_get(more_info_mod, 'certainAgreement', 'piSponsorEmployee'),
            'pointOfContact': {
                'title': _safe_get(more_info_mod, 'pointOfContact', 'title'),
                'organization': _safe_get(more_info_mod, 'pointOfContact', 'organization'),
                'email': _safe_get(more_info_mod, 'pointOfContact', 'email'),
                'phone': _safe_get(more_info_mod, 'pointOfContact', 'phone')
            } if 'pointOfContact' in more_info_mod else None
        } if more_info_mod else None
    }


def _get_with_429_retry(url, params=None, max_retries=5):
    """GET with a BOUNDED retry on HTTP 429.

    Honors the ``Retry-After`` header when present, else uses capped exponential
    backoff. Returns the final ``requests.Response``; after ``max_retries``
    consecutive 429s it returns the last (429) response so the caller can surface
    a clean error instead of looping/recursing forever.
    """
    response = None
    for attempt in range(max_retries + 1):
        _rate_limiter.wait()
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 429:
            return response
        if attempt == max_retries:
            break
        retry_after = response.headers.get("Retry-After")
        try:
            wait_s = min(60, int(retry_after)) if retry_after else min(60, 2 ** attempt)
        except (TypeError, ValueError):
            wait_s = min(60, 2 ** attempt)
        logger.warning(
            f"Rate limit hit (429), waiting {wait_s}s (attempt {attempt + 1}/{max_retries})"
        )
        time.sleep(wait_s)
    return response


def get_clinical_trial_details(nct_id: str) -> dict:
    """
    Get comprehensive clinical trial information from ClinicalTrials.gov

    Retrieves detailed information about a clinical trial including study design,
    interventions, eligibility criteria, outcomes, locations, and current status.

    Args:
        nct_id: ClinicalTrials.gov identifier (e.g., 'NCT05653258')

    Returns:
        Dictionary with comprehensive trial information:

        Identification:
        - nctId: NCT identifier
        - briefTitle: Short study title
        - officialTitle: Full official title
        - orgStudyId: Sponsor's internal study ID
        - secondaryIds: Other identifiers (NIH grants, etc.)

        Status & Timeline:
        - overallStatus: Current status (RECRUITING, COMPLETED, etc.)
        - startDate: Study start date
        - completionDate: Expected/actual completion date
        - whyStopped: Reason if terminated early
        - lastUpdatePostDate: Last update to registry

        Study Design:
        - studyType: INTERVENTIONAL, OBSERVATIONAL, etc.
        - phases: Clinical trial phases (PHASE1, PHASE2, PHASE3, PHASE4)
        - allocation: RANDOMIZED, NON_RANDOMIZED, N/A
        - interventionModel: PARALLEL, CROSSOVER, SINGLE_GROUP, etc.
        - primaryPurpose: TREATMENT, PREVENTION, DIAGNOSTIC, etc.
        - masking: SINGLE, DOUBLE, TRIPLE, QUADRUPLE, NONE
        - enrollmentCount: Number of participants

        Medical Information:
        - conditions: List of diseases/conditions studied
        - keywords: Study keywords
        - interventions: Drugs, procedures, devices being tested
        - primaryOutcomes: Primary endpoints
        - secondaryOutcomes: Secondary endpoints

        Participants:
        - eligibilityCriteria: Inclusion/exclusion criteria
        - sex: ALL, MALE, FEMALE
        - minimumAge: Minimum participant age
        - maximumAge: Maximum participant age
        - healthyVolunteers: Whether healthy volunteers accepted

        Organization:
        - leadSponsor: Primary sponsor name
        - collaborators: Collaborating organizations
        - responsibleParty: PI or sponsor responsible
        - locations: Trial sites (facility, city, country, coordinates)

        Regulatory:
        - isFdaRegulatedDrug: FDA-regulated drug
        - isFdaRegulatedDevice: FDA-regulated device
        - oversightHasDmc: Has Data Monitoring Committee

        Metadata:
        - hasResults: Whether results are posted
        - versionDate: Last data version date
        - conditionMeshTerms: MeSH terms for conditions
        - interventionMeshTerms: MeSH terms for interventions

        Results (if hasResults=True):
        - participantFlow: Enrollment, milestones, dropouts by group
        - baselineCharacteristics: Demographics and baseline measures by group
        - outcomeMeasures: Actual outcome results with statistics
          - type: PRIMARY or SECONDARY
          - title: Outcome measure name
          - description: Detailed description
          - timeFrame: When measured
          - groups: Study arms
          - classes/categories/measurements: Results by group
          - analyses: Statistical tests (p-values, confidence intervals)
        - adverseEvents: Safety data
          - groups: Number affected/at risk per group
          - seriousEvents: Serious adverse events by organ system
          - otherEvents: Other adverse events by organ system
        - moreInfo: Point of contact, agreements

    Example:
        >>> trial = get_clinical_trial_details("NCT05653258")
        >>> print(trial['briefTitle'])
        >>> print(trial['overallStatus'])
        >>> print(trial['phases'])
        >>> if trial['hasResults']:
        >>>     primary_outcome = trial['outcomeMeasures'][0]
        >>>     print(primary_outcome['title'])
        >>>     print(primary_outcome['analyses'])
    """
    try:
        # Make API request (bounded 429 retry with backoff)
        url = f"{BASE_URL}/studies/{nct_id}"
        logger.info(f"Fetching trial data for {nct_id}")

        response = _get_with_429_retry(url)

        # Still rate-limited after retries
        if response.status_code == 429:
            return {
                "success": False,
                "error": f"Rate limited by ClinicalTrials.gov after retries for {nct_id}",
            }

        # Handle not found
        if response.status_code == 404:
            return {
                "success": False,
                "error": f"Trial {nct_id} not found in ClinicalTrials.gov",
                "nctId": nct_id
            }

        # Handle other errors
        if response.status_code != 200:
            return {
                "success": False,
                "error": f"API returned status code {response.status_code}",
                "nctId": nct_id
            }

        # Parse response
        data = response.json()

        # Extract protocol section
        protocol = data.get('protocolSection', {})
        protocol_data = _extract_protocol_section(protocol)

        # Extract derived section
        derived = data.get('derivedSection', {})
        derived_data = _extract_derived_section(derived)

        # Extract results section (if available)
        results = data.get('resultsSection', {})
        results_data = _extract_results_section(results)

        # Combine all data
        result = {
            "success": True,
            "nctId": nct_id,
            "hasResults": data.get('hasResults', False),
            **protocol_data,
            **derived_data,
            **results_data
        }

        return result

    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching {nct_id}")
        return {
            "success": False,
            "error": f"Request timeout after {REQUEST_TIMEOUT} seconds",
            "nctId": nct_id
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Request error for {nct_id}: {e}")
        return {
            "success": False,
            "error": f"Network error: {str(e)}",
            "nctId": nct_id
        }

    except Exception as e:
        logger.error(f"Unexpected error for {nct_id}: {e}")
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}",
            "nctId": nct_id
        }


def clear_trial_cache():
    """
    Clear the cached trial data.

    Intentional no-op: response caching is disabled in this build for FastMCP
    compatibility, so there is nothing to clear. The tool is retained so the
    interface stays stable; it always reports success.

    Returns:
        Dictionary with a status message
    """
    return {
        "success": True,
        "message": "No-op: response caching is disabled (FastMCP compatibility)"
    }


def _build_search_params(
    condition: Optional[str] = None,
    term: Optional[str] = None,
    intervention: Optional[str] = None,
    status: Optional[List[str]] = None,
    phase: Optional[List[str]] = None,
    study_type: Optional[str] = None,
    eligibility_text: Optional[List[str]] = None,
    country: Optional[str] = "United States",
    advanced_filter: Optional[str] = None,
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> dict:
    """Build query params dict for the ClinicalTrials.gov v2 /studies endpoint."""
    params: Dict[str, Any] = {"format": "json"}

    if condition:
        params["query.cond"] = condition
    if term:
        params["query.term"] = term
    if intervention:
        params["query.intr"] = intervention
    if status:
        params["filter.overallStatus"] = ",".join(status)

    # Build filter.advanced expression
    advanced_parts: List[str] = []
    if phase:
        phase_expr = " OR ".join(phase)
        advanced_parts.append(f"AREA[Phase]({phase_expr})")
    if study_type:
        advanced_parts.append(f"AREA[StudyType]{study_type}")
    if eligibility_text:
        for et in eligibility_text:
            advanced_parts.append(f'AREA[EligibilityCriteria]"{et}"')
    if country:
        advanced_parts.append(f'AREA[LocationCountry]"{country}"')
    if advanced_filter:
        advanced_parts.append(advanced_filter)

    if advanced_parts:
        params["filter.advanced"] = " AND ".join(advanced_parts)

    params["countTotal"] = "true"
    params["pageSize"] = page_size

    if page_token:
        params["pageToken"] = page_token

    return params


def count_clinical_trials(
    condition: Optional[str] = None,
    term: Optional[str] = None,
    intervention: Optional[str] = None,
    status: Optional[List[str]] = None,
    phase: Optional[List[str]] = None,
    study_type: Optional[str] = None,
    eligibility_text: Optional[List[str]] = None,
    country: Optional[str] = "United States",
    advanced_filter: Optional[str] = None,
) -> dict:
    """
    Count clinical trials matching search criteria on ClinicalTrials.gov.

    Use this tool BEFORE search_clinical_trials to gauge how many trials match
    your query. If the count is very large, refine your filters before searching.

    All parameters are optional. Combine them to narrow your search. Every
    parameter that is supplied is ANDed together.

    Args:
        condition: Disease or condition to search for (e.g., "non-small cell lung cancer").
            Searches the Condition field specifically.
        term: General keyword search across ALL trial fields — title, summary,
            eligibility text, interventions, etc. (e.g., "KRAS G12C", "EGFR resistance").
        intervention: Drug or intervention name (e.g., "pembrolizumab", "osimertinib").
            Searches the Intervention field specifically.
        status: List of trial statuses to include. Common values:
            - "RECRUITING" — currently enrolling patients
            - "NOT_YET_RECRUITING" — approved but not yet open
            - "ACTIVE_NOT_RECRUITING" — ongoing but closed to new patients
            - "COMPLETED", "TERMINATED", "WITHDRAWN", "SUSPENDED"
            Default: None (all statuses). For patient matching, use
            ["RECRUITING", "NOT_YET_RECRUITING"].
        phase: List of clinical trial phases. Valid values:
            - "EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"
            Example: ["PHASE2", "PHASE3"] for late-stage trials.
        study_type: Type of study. Common values:
            - "INTERVENTIONAL" — testing a treatment (most relevant for matching)
            - "OBSERVATIONAL" — observational studies
        eligibility_text: List of keywords to search within the eligibility criteria
            free text (inclusion/exclusion). Each keyword is searched separately and
            ANDed together. Useful for biomarker requirements, prior therapies, etc.
            Example: ["EGFR", "T790M"] finds trials mentioning both in eligibility.
        country: Country to filter trial locations. Default: "United States".
            Set to None to search globally.
        advanced_filter: Raw Essie expression for filter.advanced. Use this for
            complex queries not covered by other parameters. Supports AREA[FieldName]
            syntax with AND/OR/NOT operators. Example:
            'AREA[EnrollmentCount]RANGE[50,MAX] AND AREA[DesignPrimaryPurpose]TREATMENT'
            See ClinicalTrials.gov API v2 documentation for all 188+ searchable fields.

    Returns:
        Dictionary containing:
        - success: Boolean indicating if the request succeeded
        - total_count: Number of matching trials
        - query_params: The parameters used for the search (for debugging/refinement)

    Example:
        >>> # How many recruiting NSCLC Phase 2/3 trials in the US?
        >>> result = count_clinical_trials(
        ...     condition="non-small cell lung cancer",
        ...     status=["RECRUITING"],
        ...     phase=["PHASE2", "PHASE3"],
        ...     study_type="INTERVENTIONAL"
        ... )
        >>> print(f"Found {result['total_count']} trials")
        >>>
        >>> # How many mention EGFR in eligibility?
        >>> result = count_clinical_trials(
        ...     condition="non-small cell lung cancer",
        ...     status=["RECRUITING"],
        ...     eligibility_text=["EGFR"]
        ... )
    """
    try:
        _rate_limiter.wait()

        params = _build_search_params(
            condition=condition,
            term=term,
            intervention=intervention,
            status=status,
            phase=phase,
            study_type=study_type,
            eligibility_text=eligibility_text,
            country=country,
            advanced_filter=advanced_filter,
            page_size=1,  # minimize payload — we only need the count
        )

        response = _get_with_429_retry(f"{BASE_URL}/studies", params=params)

        if response.status_code == 429:
            return {
                "success": False,
                "error": "Rate limited by ClinicalTrials.gov after retries",
            }

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"API returned status {response.status_code}: {response.text[:500]}",
                "query_params": params,
            }

        data = response.json()
        return {
            "success": True,
            "total_count": data.get("totalCount", 0),
            "query_params": {k: v for k, v in params.items() if k != "format"},
        }

    except requests.exceptions.Timeout:
        return {"success": False, "error": f"Request timeout after {REQUEST_TIMEOUT}s"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": f"Network error: {str(e)}"}
    except Exception as e:
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


def search_clinical_trials(
    condition: Optional[str] = None,
    term: Optional[str] = None,
    intervention: Optional[str] = None,
    status: Optional[List[str]] = None,
    phase: Optional[List[str]] = None,
    study_type: Optional[str] = None,
    eligibility_text: Optional[List[str]] = None,
    country: Optional[str] = "United States",
    advanced_filter: Optional[str] = None,
    sort: Optional[str] = None,
) -> dict:
    """
    Search ClinicalTrials.gov for clinical trials matching criteria.

    Returns FULL trial records including eligibility criteria text, interventions,
    outcomes, locations, and all metadata. Auto-paginates to return all matching
    trials (up to 1000).

    Use count_clinical_trials first to gauge result volume, then refine your
    filters so the search returns a manageable number of trials.

    For patient-trial matching workflows:
    1. Call count_clinical_trials to check volume
    2. Call search_clinical_trials with refined filters
    3. Save results to a file for the agent to reference during matching

    All parameters are optional. Combine them to narrow your search. Every
    parameter that is supplied is ANDed together.

    Args:
        condition: Disease or condition to search for (e.g., "non-small cell lung cancer").
            Searches the Condition field specifically.
        term: General keyword search across ALL trial fields — title, summary,
            eligibility text, interventions, etc. (e.g., "KRAS G12C", "EGFR resistance").
        intervention: Drug or intervention name (e.g., "pembrolizumab", "osimertinib").
            Searches the Intervention field specifically.
        status: List of trial statuses to include. Common values:
            - "RECRUITING" — currently enrolling patients
            - "NOT_YET_RECRUITING" — approved but not yet open
            - "ACTIVE_NOT_RECRUITING" — ongoing but closed to new patients
            - "COMPLETED", "TERMINATED", "WITHDRAWN", "SUSPENDED"
            Default: None (all statuses). For patient matching, use
            ["RECRUITING", "NOT_YET_RECRUITING"].
        phase: List of clinical trial phases. Valid values:
            - "EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"
            Example: ["PHASE2", "PHASE3"] for late-stage trials.
        study_type: Type of study. Common values:
            - "INTERVENTIONAL" — testing a treatment (most relevant for matching)
            - "OBSERVATIONAL" — observational studies
        eligibility_text: List of keywords to search within the eligibility criteria
            free text (inclusion/exclusion). Each keyword is searched separately and
            ANDed together. Useful for biomarker requirements, prior therapies, etc.
            Example: ["EGFR", "T790M"] finds trials mentioning both in eligibility.
        country: Country to filter trial locations. Default: "United States".
            Set to None to search globally.
        advanced_filter: Raw Essie expression for filter.advanced. Use this for
            complex queries not covered by other parameters. Supports AREA[FieldName]
            syntax with AND/OR/NOT operators. Example:
            'AREA[EnrollmentCount]RANGE[50,MAX] AND AREA[DesignPrimaryPurpose]TREATMENT'
        sort: Sort order for results. Format: "FieldName:asc" or "FieldName:desc".
            Example: "LastUpdatePostDate:desc" for most recently updated first.
            Default: relevance ranking by the API.

    Returns:
        Dictionary containing:
        - success: Boolean indicating if the request succeeded
        - total_count: Total number of matching trials
        - trials: List of full trial records. Each trial contains:
            - nctId, briefTitle, officialTitle, acronym
            - overallStatus, startDate, completionDate, lastUpdatePostDate
            - studyType, phases, allocation, interventionModel, primaryPurpose,
              masking, enrollmentCount
            - conditions, keywords
            - interventions (type, name, description, armGroupLabels)
            - armGroups (label, type, description)
            - primaryOutcomes, secondaryOutcomes
            - eligibilityCriteria (FULL TEXT of inclusion/exclusion criteria)
            - sex, minimumAge, maximumAge, healthyVolunteers
            - leadSponsor, collaborators, locations
            - conditionMeshTerms, interventionMeshTerms
        - query_params: The parameters used (for debugging)
        - pages_fetched: Number of API pages retrieved

    Example:
        >>> # Find all recruiting EGFR NSCLC trials in the US
        >>> result = search_clinical_trials(
        ...     condition="non-small cell lung cancer",
        ...     term="EGFR",
        ...     status=["RECRUITING"],
        ...     phase=["PHASE2", "PHASE3"],
        ...     study_type="INTERVENTIONAL"
        ... )
        >>> print(f"Found {result['total_count']} trials")
        >>> for trial in result['trials']:
        ...     print(f"{trial['nctId']}: {trial['briefTitle']}")
        ...     print(f"  Eligibility: {trial['eligibilityCriteria'][:200]}...")
        >>>
        >>> # Search by biomarker in eligibility text
        >>> result = search_clinical_trials(
        ...     condition="non-small cell lung cancer",
        ...     status=["RECRUITING"],
        ...     eligibility_text=["KRAS", "G12C"],
        ...     study_type="INTERVENTIONAL"
        ... )
        >>>
        >>> # Search for post-osimertinib trials
        >>> result = search_clinical_trials(
        ...     condition="NSCLC",
        ...     status=["RECRUITING"],
        ...     eligibility_text=["osimertinib", "progression"],
        ...     study_type="INTERVENTIONAL"
        ... )
    """
    try:
        all_trials = []
        page_token = None
        pages_fetched = 0
        total_count = None

        # Build base params once (for return value and first request)
        base_params = _build_search_params(
            condition=condition,
            term=term,
            intervention=intervention,
            status=status,
            phase=phase,
            study_type=study_type,
            eligibility_text=eligibility_text,
            country=country,
            advanced_filter=advanced_filter,
            page_size=100,
        )
        if sort:
            base_params["sort"] = sort

        trunc_warning = None
        while True:
            # Copy base params and set page token for this iteration
            params = dict(base_params)
            if page_token:
                params["pageToken"] = page_token

            response = _get_with_429_retry(f"{BASE_URL}/studies", params=params)

            if response.status_code == 429:
                # Exhausted retries — surface partial results if any, else error
                if all_trials:
                    trunc_warning = "Rate limited by ClinicalTrials.gov after retries — partial results"
                    break
                return {
                    "success": False,
                    "error": "Rate limited by ClinicalTrials.gov after retries",
                    "query_params": params,
                }

            if response.status_code != 200:
                if all_trials:
                    # Return what we have so far, but flag it as incomplete
                    logger.warning(
                        f"API error on page {pages_fetched + 1}, returning partial results"
                    )
                    trunc_warning = (
                        f"API error {response.status_code} on page {pages_fetched + 1} — "
                        "partial results; total_count reflects rows returned, not the full match count"
                    )
                    break
                return {
                    "success": False,
                    "error": f"API returned status {response.status_code}: {response.text[:500]}",
                    "query_params": params,
                }

            data = response.json()
            pages_fetched += 1

            if total_count is None:
                total_count = data.get("totalCount", 0)

            # Parse each study using existing extraction logic
            for study in data.get("studies", []):
                protocol = study.get("protocolSection", {})
                derived = study.get("derivedSection", {})

                trial_record = _extract_protocol_section(protocol)
                trial_record.update(_extract_derived_section(derived))
                trial_record["hasResults"] = study.get("hasResults", False)
                all_trials.append(trial_record)

            # Check for next page
            next_token = data.get("nextPageToken")
            if not next_token or len(all_trials) >= 1000:
                break
            page_token = next_token

        result = {
            "success": True,
            "total_count": len(all_trials) if trunc_warning else (total_count or len(all_trials)),
            "trials": all_trials,
            "trials_returned": len(all_trials),
            "query_params": {
                k: v for k, v in base_params.items()
                if k not in ("format", "pageSize", "pageToken")
            },
            "pages_fetched": pages_fetched,
        }
        if trunc_warning:
            result["truncated"] = True
            result["warning"] = trunc_warning
        return result

    except requests.exceptions.Timeout:
        if all_trials:
            return {
                "success": True,
                "total_count": total_count or len(all_trials),
                "trials": all_trials,
                "trials_returned": len(all_trials),
                "pages_fetched": pages_fetched,
                "warning": "Timeout during pagination — partial results returned",
            }
        return {"success": False, "error": f"Request timeout after {REQUEST_TIMEOUT}s"}
    except requests.exceptions.RequestException as e:
        if all_trials:
            return {
                "success": True,
                "total_count": total_count or len(all_trials),
                "trials": all_trials,
                "trials_returned": len(all_trials),
                "pages_fetched": pages_fetched,
                "warning": f"Network error during pagination — partial results: {str(e)}",
            }
        return {"success": False, "error": f"Network error: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected error in search_clinical_trials: {e}")
        return {"success": False, "error": f"Unexpected error: {str(e)}"}


# ============================================================================
# cBioPortal Tools - Cancer Genomics Data
# ============================================================================

def get_all_cancer_types() -> dict:
    """
    Get all cancer types available in cBioPortal

    Provides the controlled vocabulary of cancer type IDs that agents should use
    when querying for cancer-specific studies and data.

    Returns:
        Dictionary containing:
        - success: Boolean indicating if request succeeded
        - cancer_types: List of dictionaries with:
            - cancerTypeId: Short identifier (e.g., 'paad', 'gbm', 'luad')
            - name: Full cancer type name (e.g., 'Pancreatic Adenocarcinoma')
            - parent: Parent cancer type ID (tissue of origin)
            - color: Hex color code for visualization
        - count: Total number of cancer types

    Example:
        >>> result = get_all_cancer_types()
        >>> print(f"Found {result['count']} cancer types")
        >>> # Find pancreatic cancer ID
        >>> pancreatic = [ct for ct in result['cancer_types']
        ...               if 'pancreatic' in ct['name'].lower()]
    """
    try:
        logger.info("Fetching all cancer types from cBioPortal")

        # Get cancer types DataFrame
        df = cancer_types.get_all_cancer_types()

        # Convert to list of dictionaries
        cancer_type_list = []
        for _, row in df.iterrows():
            cancer_type_list.append({
                'cancerTypeId': row.get('cancerTypeId'),
                'name': row.get('name'),
                'parent': row.get('parent'),
                'color': row.get('dedicatedColor')
            })

        return {
            "success": True,
            "cancer_types": cancer_type_list,
            "count": len(cancer_type_list)
        }

    except Exception as e:
        logger.error(f"Error fetching cancer types: {e}")
        return {
            "success": False,
            "error": f"Failed to retrieve cancer types: {str(e)}",
            "cancer_types": [],
            "count": 0
        }


def search_studies(cancer_type: Optional[str] = None) -> dict:
    """
    Search for cancer genomics studies in cBioPortal

    Find relevant datasets by cancer type. Use this to discover available studies
    before downloading data. Use get_study_details() to check what molecular data
    (mutations, CNA, expression) is available in each study.

    Args:
        cancer_type: Optional cancer type ID (e.g., 'paad', 'gbm', 'luad').
                    Use get_all_cancer_types() to see valid IDs.
                    If None, returns all studies.

    Returns:
        Dictionary containing:
        - success: Boolean indicating if request succeeded
        - studies: List of dictionaries with:
            - studyId: Unique study identifier
            - name: Full study name
            - description: Study description
            - cancerTypeId: Cancer type
            - pmid: PubMed ID (if available)
            - citation: Publication citation (if available)
            Note: Sample counts are NOT included in search results due to API limitations.
                  Use get_study_details() to get accurate sample counts by data type.
        - count: Number of studies found
        - filters_applied: Dict showing which filters were used

    Example:
        >>> # Find all pancreatic cancer studies
        >>> result = search_studies(cancer_type='paad')
        >>>
        >>> # Get all studies (no filter)
        >>> result = search_studies()
        >>>
        >>> # Then check what data each study has
        >>> details = get_study_details('paad_tcga')
        >>> has_mutations = any(p['molecularAlterationType'] == 'MUTATION_EXTENDED'
        ...                     for p in details['molecular_profiles'])
    """
    try:
        logger.info(f"Searching studies: cancer_type={cancer_type}")

        # Get all studies
        df = studies.get_all_studies()

        # Apply cancer type filter if specified
        if cancer_type:
            df = df[df['cancerTypeId'] == cancer_type]

        # Convert to list of dictionaries
        study_list = []
        for _, row in df.iterrows():
            study_list.append({
                'studyId': row.get('studyId'),
                'name': row.get('name'),
                'description': row.get('description'),
                'cancerTypeId': row.get('cancerTypeId'),
                'pmid': row.get('pmid'),
                'citation': row.get('citation')
            })
            # Note: allSampleCount is unreliable in search results (often returns 1)
            # Use get_study_details() to get accurate sample counts by data type

        return {
            "success": True,
            "studies": study_list,
            "count": len(study_list),
            "filters_applied": {
                "cancer_type": cancer_type
            }
        }

    except Exception as e:
        logger.error(f"Error searching studies: {e}")
        return {
            "success": False,
            "error": f"Failed to search studies: {str(e)}",
            "studies": [],
            "count": 0
        }


def get_study_details(study_id: str) -> dict:
    """
    Get comprehensive details about a specific study

    Retrieves detailed information including available molecular profiles
    (mutation, CNA, expression, etc.), sample counts, and clinical attributes.
    Use this before downloading data to understand what's available.

    Args:
        study_id: Study identifier (e.g., 'paad_tcga', 'gbm_tcga', 'luad_tcga')

    Returns:
        Dictionary containing:
        - success: Boolean indicating if request succeeded
        - studyId: Study identifier
        - name: Full study name
        - description: Study description
        - cancerTypeId: Cancer type
        - sample_counts: Dictionary of sample counts by data type:
            - sequenced: Samples with mutation data
            - cna: Samples with copy number data
            - rna_seq: Samples with RNA-seq expression data
            - rppa: Samples with protein data (RPPA)
            - complete: Samples with all data types
        - citation: Publication citation
        - pmid: PubMed ID
        - molecular_profiles: List of available data types:
            - molecularProfileId: Profile identifier
            - molecularAlterationType: Type (MUTATION_EXTENDED, COPY_NUMBER_ALTERATION,
                                            MRNA_EXPRESSION, etc.)
            - datatype: Data format (MAF, DISCRETE, CONTINUOUS, Z-SCORE)
            - name: Profile description
        - clinical_attributes: List of available clinical data fields:
            - clinicalAttributeId: Attribute identifier
            - displayName: Human-readable name
            - datatype: STRING, NUMBER, BOOLEAN
            - priority: Display priority

    Example:
        >>> details = get_study_details('paad_tcga')
        >>> print(f"Study: {details['name']}")
        >>> print(f"Mutation samples: {details['sample_counts']['sequenced']}")
        >>> print(f"CNA samples: {details['sample_counts']['cna']}")
        >>> print(f"Expression samples: {details['sample_counts']['rna_seq']}")
        >>> print("Available data:")
        >>> for profile in details['molecular_profiles']:
        >>>     print(f"  - {profile['molecularAlterationType']}")
    """
    try:
        logger.info(f"Fetching details for study: {study_id}")

        # Get study metadata
        study_df = studies.get_study(study_id=study_id)

        if study_df.empty:
            return {
                "success": False,
                "error": f"Study '{study_id}' not found",
                "studyId": study_id
            }

        study_row = study_df.iloc[0]

        # Get molecular profiles
        profiles_df = molecular_profiles.get_all_molecular_profiles_in_study(study_id=study_id)
        profile_list = []
        for _, prof in profiles_df.iterrows():
            profile_list.append({
                'molecularProfileId': prof.get('molecularProfileId'),
                'molecularAlterationType': prof.get('molecularAlterationType'),
                'datatype': prof.get('datatype'),
                'name': prof.get('name'),
                'description': prof.get('description')
            })

        # Get clinical attributes
        clinical_attrs_df = clinical_attributes.get_all_clinical_attributes_in_study(study_id=study_id)
        clinical_attr_list = []
        for _, attr in clinical_attrs_df.iterrows():
            clinical_attr_list.append({
                'clinicalAttributeId': attr.get('clinicalAttributeId'),
                'displayName': attr.get('displayName'),
                'datatype': attr.get('datatype'),
                'priority': attr.get('priority'),
                'description': attr.get('description')
            })

        return {
            "success": True,
            "studyId": study_id,
            "name": study_row.get('name'),
            "description": study_row.get('description'),
            "cancerTypeId": study_row.get('cancerTypeId'),
            "sample_counts": {
                "sequenced": int(study_row.get('sequencedSampleCount', 0)),
                "cna": int(study_row.get('cnaSampleCount', 0)),
                "rna_seq": int(study_row.get('mrnaRnaSeqV2SampleCount', 0)),
                "rppa": int(study_row.get('rppaSampleCount', 0)),
                "complete": int(study_row.get('completeSampleCount', 0))
            },
            "citation": study_row.get('citation'),
            "pmid": study_row.get('pmid'),
            "molecular_profiles": profile_list,
            "clinical_attributes": clinical_attr_list
        }

    except Exception as e:
        logger.error(f"Error fetching study details for {study_id}: {e}")
        return {
            "success": False,
            "error": f"Failed to retrieve study details: {str(e)}",
            "studyId": study_id
        }


def get_clinical_data(study_id: str, sample_ids: Optional[List[str]] = None) -> dict:
    """
    Get clinical data for samples in a study

    Downloads BOTH sample-level and patient-level clinical data, merging them together.
    Patient-level data (survival, demographics) is shared across all samples from the same patient.

    Args:
        study_id: Study identifier (e.g., 'paad_tcga', 'gbm_tcga')
        sample_ids: Optional list of sample IDs to retrieve. If None, gets all samples.

    Returns:
        Dictionary containing:
        - success: Boolean indicating if request succeeded
        - study_id: Study identifier
        - sample_count: Number of samples with data
        - clinical_attributes: List of available clinical attribute names
        - data: List of sample-level clinical records with fields:
            - sampleId: Sample identifier
            - patientId: Patient identifier
            - <attribute_name>: Value for each clinical attribute
                Sample-level attributes:
                - SAMPLE_TYPE (Primary, Metastatic, etc.)
                - TUMOR_TISSUE_SITE
                - FRACTION_GENOME_ALTERED
                Patient-level attributes (shared across patient's samples):
                - AGE / AGE_AT_DIAGNOSIS
                - SEX / GENDER
                - STAGE / TUMOR_STAGE
                - GRADE / TUMOR_GRADE
                - OS_STATUS / OS_MONTHS (overall survival)
                - DFS_STATUS / DFS_MONTHS (disease-free survival)
                - TREATMENT / THERAPY_TYPE

    Example:
        >>> # Get all clinical data for a study
        >>> result = get_clinical_data('paad_tcga')
        >>>
        >>> # Calculate median survival (patient-level data)
        >>> os_months = [float(s.get('OS_MONTHS')) for s in result['data']
        ...              if s.get('OS_MONTHS') is not None]
        >>> median_os = statistics.median(os_months)
    """
    try:
        logger.info(f"Fetching clinical data for {study_id}")

        # Get samples if not specified
        if sample_ids is None:
            samples_df = samples.get_all_samples_in_study(study_id=study_id)
            sample_ids = samples_df['sampleId'].tolist()
            patient_map = dict(zip(samples_df['sampleId'], samples_df['patientId']))
        else:
            # Get patient IDs for specified samples
            samples_df = samples.get_all_samples_in_study(study_id=study_id)
            samples_df = samples_df[samples_df['sampleId'].isin(sample_ids)]
            patient_map = dict(zip(samples_df['sampleId'], samples_df['patientId']))

        if not sample_ids:
            return {
                "success": False,
                "error": "No samples found",
                "study_id": study_id
            }

        # Get all clinical attributes for this study
        clinical_attrs = clinical_attributes.get_all_clinical_attributes_in_study(study_id=study_id)
        attr_names = clinical_attrs['clinicalAttributeId'].tolist()

        # Initialize data structure
        sample_clinical_data = {}
        for sample_id in sample_ids:
            sample_clinical_data[sample_id] = {
                'sampleId': sample_id,
                'patientId': patient_map.get(sample_id)
            }

        # Fetch SAMPLE-level clinical data
        sample_clinical_df = cbio_clinical.get_all_clinical_data_in_study(
            study_id=study_id,
            clinical_data_type='SAMPLE'
        )

        # Filter to requested samples if provided
        if sample_ids:
            sample_clinical_df = sample_clinical_df[sample_clinical_df['sampleId'].isin(sample_ids)]

        # Populate sample-level attributes
        for _, row in sample_clinical_df.iterrows():
            sample_id = row.get('sampleId')
            attr_id = row.get('clinicalAttributeId')
            value = row.get('value')

            if sample_id in sample_clinical_data:
                sample_clinical_data[sample_id][attr_id] = value

        # Fetch PATIENT-level clinical data (includes survival)
        patient_clinical_df = cbio_clinical.get_all_clinical_data_in_study(
            study_id=study_id,
            clinical_data_type='PATIENT'
        )

        # Get unique patient IDs from our samples
        patient_ids = list(set(patient_map.values()))
        patient_clinical_df = patient_clinical_df[patient_clinical_df['patientId'].isin(patient_ids)]

        # Create patient data lookup
        patient_data = {}
        for _, row in patient_clinical_df.iterrows():
            patient_id = row.get('patientId')
            attr_id = row.get('clinicalAttributeId')
            value = row.get('value')

            if patient_id not in patient_data:
                patient_data[patient_id] = {}
            patient_data[patient_id][attr_id] = value

        # Merge patient-level data into sample records
        for sample_id, data in sample_clinical_data.items():
            patient_id = data['patientId']
            if patient_id in patient_data:
                # Add all patient-level attributes to this sample
                data.update(patient_data[patient_id])

        return {
            "success": True,
            "study_id": study_id,
            "sample_count": len(sample_clinical_data),
            "clinical_attributes": attr_names,
            "data": list(sample_clinical_data.values())
        }

    except Exception as e:
        logger.error(f"Error fetching clinical data: {e}")
        return {
            "success": False,
            "error": f"Failed to retrieve clinical data: {str(e)}",
            "study_id": study_id
        }
