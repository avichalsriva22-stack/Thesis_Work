from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from shapely.geometry import Point, LineString

# --- Phase 1: Anchored Data Models ---
@dataclass
class AnchoredOvertureConnector:
    id: str
    geometry: Point  # UTM projected

@dataclass
class SegmentConnectorRef:
    id: str
    at: float

@dataclass
class AnchoredOvertureSegment:
    id: str
    geometry: LineString  # UTM projected
    connectors: List[SegmentConnectorRef]
    width_rules: List[Dict[str, Any]] = field(default_factory=list)
    subtype: Optional[str] = None
    road_class: Optional[str] = None
    subclass: Optional[str] = None
    sources: List[Dict[str, str]] = field(default_factory=list)

@dataclass
class AnchoredOSMNode:
    id: int
    version: int
    geometry: Point  # UTM projected
    tags: Dict[str, str] = field(default_factory=dict)

@dataclass
class AnchoredOSMWay:
    id: int
    version: int
    nodes: List[int] # node IDs
    tags: Dict[str, str] = field(default_factory=dict)

# --- Phase 2: OpenDRIVE Intermediate Topology Models ---

@dataclass
class PlanViewPoint:
    s: float
    x: float
    y: float
    heading: float
    curvature: float

@dataclass
class GeometricPrimitive:
    s: float
    x: float
    y: float
    heading: float
    length: float

@dataclass
class LinePrimitive(GeometricPrimitive):
    pass

@dataclass
class ArcPrimitive(GeometricPrimitive):
    curvature: float

@dataclass
class SpiralPrimitive(GeometricPrimitive):
    curvature_start: float
    curvature_end: float

# --- Phase 3: Semantic Enrichment Models ---

@dataclass
class SemanticSignal:
    id: str
    s: float
    t: float
    type: str # e.g. 'traffic_light', 'stop_sign'
    orientation: str # '+', '-', or 'none'

@dataclass
class LaneSemantics:
    turn_rules: List[str]         # e.g. ['left', 'through', 'right']
    n_forward: int = 1            # Lanes in forward direction (right side)
    n_backward: int = 0           # Lanes in backward/opposing direction (left side)
    is_oneway: bool = False       # True if OSM oneway=yes
    osm_width_m: float = 0.0     # Width in metres from OSM width= tag (0 = unknown)

@dataclass
class PlanViewGeometry:
    length: float
    points: List[PlanViewPoint] = field(default_factory=list) # For spline strategy
    primitives: List[GeometricPrimitive] = field(default_factory=list) # For primitive strategy

@dataclass
class RoadLink:
    element_type: str # 'road' or 'junction'
    element_id: str
    contact_point: str # 'start' or 'end'

@dataclass
class RoadNode:
    id: str # Will map to Overture segment id
    geometry: PlanViewGeometry
    predecessor: Optional[RoadLink] = None
    successor: Optional[RoadLink] = None
    lane_widths: List[Dict[str, Any]] = field(default_factory=list)
    signals: List[SemanticSignal] = field(default_factory=list)
    lane_semantics: Optional[LaneSemantics] = None

@dataclass
class LaneLink:
    from_lane: int
    to_lane: int

@dataclass
class JunctionConnection:
    incoming_road: str
    connecting_road: str
    contact_point: str
    lane_links: List["LaneLink"] = field(default_factory=list)
    turn_angle_deg: float = 0.0
    physical_road_id: Optional[str] = None

@dataclass
class JunctionNode:
    id: str # Will map to OSM node id
    connected_roads: List[Tuple[str, str]] = field(default_factory=list) # List of (seg_id, contact_point)
    connections: List[JunctionConnection] = field(default_factory=list)
