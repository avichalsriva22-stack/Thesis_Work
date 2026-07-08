import re
from typing import List, Optional

def parse_turn_lanes(turn_tags: str) -> List[str]:
    """
    Parses OSM 'turn:lanes' or 'turn' string tags into an ordered list of turn rules.
    OSM uses '|' to separate lanes (left to right), and ';' to separate multiple rules per lane.
    Example: 'left|through;right|right' -> ['left', 'through;right', 'right']
    """
    if not turn_tags:
        return []
        
    # Standardize empty strings and clean whitespace
    lanes = turn_tags.strip().split('|')
    parsed_rules = []
    
    for lane in lanes:
        lane = lane.strip()
        if not lane or lane == 'none':
            parsed_rules.append('none')
        else:
            # We preserve the ';' so the downstream OpenDRIVE lane validity logic
            # can generate multiple validities if a single lane allows through AND right.
            parsed_rules.append(lane)
            
    return parsed_rules

def parse_traffic_signals(tags: dict) -> Optional[str]:
    """
    Analyzes an OSM node's tags to determine if it is a signal or a sign.
    Returns the OpenDRIVE type string, or None if it's not a relevant feature.
    """
    highway = tags.get('highway', '')
    
    if highway == 'traffic_signals':
        return 'traffic_light'
    elif highway == 'stop':
        return 'stop_sign'
    elif highway == 'give_way':
        return 'yield_sign'
    elif highway == 'crossing':
        return 'crosswalk'
        
    return None
