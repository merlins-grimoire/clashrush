"""Transient-frame recognition; no capture, file write, OCR or native input.

Adapted algorithmic seams (see docs/deployment-donor-notices.md):
* CoC_Bot a5c943a: attacker.detect_troop_positions, bounded Sobel/peak/card edges.
* ClashAutomation c41fe12: detect_base_bbox + detect_deploy_ring.

The original gap-based class guesses, default counts, point clamping, debug
writes and configured-point fallback are explicitly NOT retained. Geometry
proposes points. A separate current forbidden-boundary observation authorizes
legal ground. Source artwork is not a runtime calibration certificate.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import cv2
import numpy as np
from scipy.signal import find_peaks

from .mvp_deployment import DeploymentError, Point, finite


MAX_FRAME_BYTES = 64 * 1024 * 1024


def image(frame: object, *, gray: bool = False) -> np.ndarray:
    if (type(frame) is not np.ndarray or frame.dtype != np.uint8
            or frame.size == 0 or frame.nbytes > MAX_FRAME_BYTES
            or frame.ndim not in ((2, 3) if gray else (3,))
            or (frame.ndim == 3 and frame.shape[2] != 3)
            or min(frame.shape[:2]) < 1):
        raise DeploymentError('IMAGE_INVALID')
    return frame


def roi(frame: np.ndarray, box: tuple[float,float,float,float]) -> np.ndarray:
    image(frame, gray=True)
    if type(box) not in (tuple,list) or len(box)!=4:
        raise DeploymentError('ROI_INVALID')
    x0,y0,x1,y1=(finite(v,0,1) for v in box)
    if not (x0<x1 and y0<y1): raise DeploymentError('ROI_INVALID')
    h,w=frame.shape[:2]
    left,right=int(w*x0),int(w*x1)
    top,bottom=int(h*y0),int(h*y1)
    if right<=left or bottom<=top: raise DeploymentError('ROI_EMPTY')
    return frame[top:bottom,left:right]


def _gray(frame):
    return cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY) if frame.ndim==3 else frame


def matches(frame, template, threshold: float=.92, *, maximum=32):
    image(frame,gray=True); image(template,gray=True)
    finite(threshold,.5,1)
    a,b=_gray(frame),_gray(template)
    if b.shape[0]>a.shape[0] or b.shape[1]>a.shape[1]: return ()
    if float(b.std()) < 1: raise DeploymentError('TEMPLATE_FLAT')
    scores=cv2.matchTemplate(a,b,cv2.TM_CCOEFF_NORMED)
    if not np.isfinite(scores).all(): raise DeploymentError('MATCH_NONFINITE')
    result=[]; th,tw=b.shape
    for _ in range(maximum+1):
        _,score,_,(x,y)=cv2.minMaxLoc(scores)
        if score<threshold: return tuple(result)
        if len(result)>=maximum: raise DeploymentError('MATCH_LIMIT')
        result.append((x,y,tw,th,float(score)))
        # All highly correlated translations around this same occurrence collapse
        # to one candidate; disjoint copies remain separately observable.
        scores[max(0,y-th//2):min(scores.shape[0],y+th//2+1),
               max(0,x-tw//2):min(scores.shape[1],x+tw//2+1)]=-1
    raise DeploymentError('MATCH_LIMIT')


def match_unique(frame,template,threshold=.92):
    found=matches(frame,template,threshold,maximum=2)
    if len(found)>1: raise DeploymentError('MATCH_AMBIGUOUS')
    return found[0] if found else None


@dataclass(frozen=True, slots=True)
class CardGeometry:
    card_width: float=.068
    width_tolerance: float=.010
    peak_height: float=.8
    peak_distance: int=10
    def __post_init__(self):
        finite(self.card_width,.03,.25); finite(self.width_tolerance,.001,.02)
        finite(self.peak_height,.5,1)
        if type(self.peak_distance) is not int or not 1<=self.peak_distance<=40:
            raise DeploymentError('GEOMETRY_INVALID')


def discover_card_boxes(bar: np.ndarray, geometry: CardGeometry=CardGeometry()):
    """Return complete-card pixel boxes in this ROI's coordinate system.

    Caller adds the actual ROI origin exactly once. Partial outside cards are
    not silently declared exhausted; the page's independent edge evidence and
    overlap tracker determine whether more cards exist.
    """
    image(bar)
    if type(geometry) is not CardGeometry: raise DeploymentError('GEOMETRY_INVALID')
    h,w=bar.shape[:2]
    gray=cv2.equalizeHist(_gray(bar))
    edges=cv2.convertScaleAbs(np.abs(cv2.Sobel(gray,cv2.CV_64F,1,0,ksize=3)))
    profile=np.sum(edges,axis=0,dtype=np.float64)
    spread=float(np.ptp(profile))
    if not math.isfinite(spread) or spread<=0: raise DeploymentError('BAR_UNPROVED')
    profile=(profile-profile.min())/spread
    peaks=find_peaks(profile,height=geometry.peak_height,distance=geometry.peak_distance)[0]
    if len(peaks)<2 or len(peaks)>64: raise DeploymentError('BAR_UNPROVED')
    # CoC_Bot's donor grammar alternates card-width spans with one of two
    # observed inter-card gap classes. Selecting the longest complete run keeps
    # a strong translucent-bar/scenery edge outside the card sequence instead
    # of pairing it as a phantom first card.
    def near(value,target,tolerance): return abs(value-target)<=tolerance
    gaps=(.007,.015); gap_tolerance=.01
    runs=[]
    for start in range(len(peaks)-1):
        left,right=int(peaks[start]),int(peaks[start+1])
        if not near((right-left)/w,geometry.card_width,geometry.width_tolerance):
            continue
        boxes=[(left,0,right,h)]; index=start+1
        while index+2<len(peaks):
            gap=(int(peaks[index+1])-int(peaks[index]))/w
            next_left,next_right=int(peaks[index+1]),int(peaks[index+2])
            if (not any(near(gap,expected,gap_tolerance) for expected in gaps)
                    or not near((next_right-next_left)/w,geometry.card_width,geometry.width_tolerance)):
                break
            boxes.append((next_left,0,next_right,h)); index+=2
        runs.append(tuple(boxes))
    if not runs: raise DeploymentError('BAR_UNPROVED')
    longest=max(map(len,runs))
    winners={run for run in runs if len(run)==longest}
    if len(winners)!=1: raise DeploymentError('BAR_AMBIGUOUS')
    boxes=winners.pop()
    if len(boxes)>24: raise DeploymentError('BAR_UNPROVED')
    return boxes


def classify_icon(crop, catalog: Mapping[str,tuple[np.ndarray,...]], threshold=.93, margin=.04):
    image(crop); finite(threshold,.8,1); finite(margin,.001,.2)
    if not 1<=len(catalog)<=96: raise DeploymentError('CATALOG_INVALID')
    scores=[]
    for identity,variants in catalog.items():
        if type(identity) is not str or type(variants) is not tuple or not variants:
            raise DeploymentError('CATALOG_INVALID')
        best=-1.0
        for template in variants:
            found=match_unique(crop,template,threshold)
            if found: best=max(best,found[-1])
        if best>=threshold: scores.append((best,identity))
    scores.sort(reverse=True)
    if not scores: return None
    if len(scores)>1 and scores[0][0]-scores[1][0]<margin:
        raise DeploymentError('CARD_CLASS_AMBIGUOUS')
    return scores[0][1]


def exact_grey(crop) -> bool:
    """Existing <80 saturation feature. MUST NOT stand alone as depletion proof."""
    image(crop)
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    value=float(np.mean(hsv[:,:,1],dtype=np.float64))
    if not math.isfinite(value): raise DeploymentError('DEPLETION_UNPROVED')
    return bool(value < 80.0)


def _tight(binary):
    ys,xs=np.where(binary>0)
    if not len(xs): raise DeploymentError('GLYPH_EMPTY')
    return binary[ys.min():ys.max()+1,xs.min():xs.max()+1]


def _normal_glyph(binary):
    a=_tight(binary)
    # Fit into a fixed canvas preserving aspect ratio. No font or OCR model.
    h,w=a.shape
    scale=min(24/w,40/h)
    resized=cv2.resize(a,(max(1,round(w*scale)),max(1,round(h*scale))),interpolation=cv2.INTER_NEAREST)
    out=np.zeros((44,28),np.uint8)
    y=(44-resized.shape[0])//2; x=(28-resized.shape[1])//2
    out[y:y+resized.shape[0],x:x+resized.shape[1]]=resized
    return out>0


def read_quantity(crop, glyphs: Mapping[str,np.ndarray], maximum: int,
                  *, threshold: int=160, minimum_score: float=.92, margin: float=.04) -> int:
    """Closed x[0-9]{1,3} reader; every glyph is consumed, no default value.

    The exact count ROI and glyph bank must be privately calibrated. A whole
    field—not an arbitrary substring—is matched. No unrestricted OCR is used.
    """
    image(crop,gray=True)
    if set(glyphs)!=set('x0123456789') or type(maximum) is not int or not 0<=maximum<=999:
        raise DeploymentError('QUANTITY_POLICY_INVALID')
    if type(threshold) is not int or not 0<threshold<255: raise DeploymentError('QUANTITY_POLICY_INVALID')
    finite(minimum_score,.8,1); finite(margin,.001,.2)
    binary=(_gray(crop)>=threshold).astype(np.uint8)*255
    active=np.any(binary>0,axis=0)
    padded=np.pad(active.astype(np.int8),(1,1))
    starts=np.flatnonzero(np.diff(padded)==1); ends=np.flatnonzero(np.diff(padded)==-1)
    if not 2<=len(starts)<=4: raise DeploymentError('QUANTITY_UNKNOWN')
    # Text touching a field boundary may be clipped, so it cannot authorize input.
    if starts[0]==0 or ends[-1]==crop.shape[1] or np.any(binary[0]) or np.any(binary[-1]):
        raise DeploymentError('QUANTITY_CLIPPED')
    bank={key:_normal_glyph((_gray(image(value,gray=True))>=threshold).astype(np.uint8)*255)
          for key,value in glyphs.items()}
    text=''
    for left,right in zip(starts,ends):
        normal=_normal_glyph(binary[:,left:right])
        ranks=[]
        for key,glyph in bank.items():
            union=int(np.count_nonzero(normal|glyph))
            score=float(np.count_nonzero(normal&glyph))/union if union else 0
            ranks.append((score,key))
        ranks.sort(reverse=True)
        if ranks[0][0]<minimum_score or ranks[0][0]-ranks[1][0]<margin:
            raise DeploymentError('QUANTITY_UNKNOWN')
        text+=ranks[0][1]
    if not text.startswith('x') or not text[1:].isdigit() or (len(text)>2 and text[1]=='0'):
        raise DeploymentError('QUANTITY_UNKNOWN')
    value=int(text[1:])
    if value>maximum: raise DeploymentError('QUANTITY_RANGE')
    return value


def base_bbox(frame, *, central=(.13,.13,.87,.80), percentile=4.0):
    image(frame); finite(percentile,0,10)
    h,w=frame.shape[:2]
    x0,y0,x1,y1=central
    for v in central: finite(v,0,1)
    if not(x0<x1 and y0<y1): raise DeploymentError('GEOMETRY_INVALID')
    edges=cv2.Canny(cv2.GaussianBlur(_gray(frame),(3,3),0),50,150)
    ys,xs=np.where(edges[int(y0*h):int(y1*h),int(x0*w):int(x1*w)]>0)
    if len(xs)<50: return None
    xs=xs+int(x0*w); ys=ys+int(y0*h)
    return (int(np.percentile(xs,percentile)),int(np.percentile(ys,percentile)),
            int(np.percentile(xs,100-percentile)),int(np.percentile(ys,100-percentile)))


def candidate_ring(frame, *, safe=(.06,.12,.94,.80), directions=24,
                   margin_ref=75.0, density_ground=.06):
    """ClashAutomation's ray/edge-density candidates, without clamping or fallback."""
    image(frame)
    if type(directions) is not int or not 4<=directions<=24: raise DeploymentError('GEOMETRY_INVALID')
    finite(margin_ref,1,200); finite(density_ground,.001,1)
    h,w=frame.shape[:2]; bb=base_bbox(frame)
    if bb is None: return ()
    sx0,sy0,sx1,sy1=(finite(v,0,1) for v in safe)
    cx,cy=(bb[0]+bb[2])//2,(bb[1]+bb[3])//2
    edges=cv2.Canny(cv2.GaussianBlur(_gray(frame),(3,3),0),50,150).astype(np.float32)
    # Scale the spatial averaging window together with the reference margin.
    blur=max(3,round(61*w/1728))|1
    density=cv2.blur(edges,(blur,blur))
    if density.max()<=0: return ()
    density/=density.max(); out=[]
    for k in range(directions):
        angle=2*math.pi*k/directions; dx,dy=math.cos(angle),math.sin(angle)
        last=20
        for r in range(20,max(w,h),max(1,round(8*w/1728))):
            x,y=round(cx+dx*r),round(cy+dy*r)
            if not(sx0*w<x<sx1*w and sy0*h<y<sy1*h): break
            if density[y,x]>density_ground: last=r
        r=last+margin_ref*w/1728
        x,y=round(cx+dx*r),round(cy+dy*r)
        if sx0*w<x<sx1*w and sy0*h<y<sy1*h:
            p=Point(x/(w-1),y/(h-1))
            if not any(p.near(q,.002) for q in out): out.append(p)
    return tuple(out)


def validated_ground(frame, candidates, *, overlay_hsv, playfield,
                     margin_pixels=12, min_boundary_fraction=.03):
    """Conservative overlay+playfield gate, separate from density proposals.

    Requires a calibrated visible closed no-deploy boundary. No boundary means
    NO permission. The profile review must establish the overlay's semantic
    meaning, completeness and supported view; this code cannot infer that from
    arbitrary red pixels. Open/clipped/degenerate observations fail closed.
    """
    image(frame)
    if overlay_hsv is None or playfield is None: return ()
    if type(margin_pixels) is not int or not 1<=margin_pixels<=128:
        raise DeploymentError('GEOMETRY_INVALID')
    if not isinstance(overlay_hsv,(tuple,list)) or len(overlay_hsv)!=2:
        raise DeploymentError('GEOMETRY_INVALID')
    lower,upper=overlay_hsv
    for row in (lower,upper):
        if len(row)!=3 or any(type(v) is not int or not 0<=v<=255 for v in row):
            raise DeploymentError('GEOMETRY_INVALID')
    if lower[0]>179 or upper[0]>179 or any(a>b for a,b in zip(lower,upper)):
        raise DeploymentError('GEOMETRY_INVALID')
    h,w=frame.shape[:2]
    polygon=[]
    if not 3<=len(playfield)<=16: raise DeploymentError('GEOMETRY_INVALID')
    for pair in playfield:
        if len(pair)!=2: raise DeploymentError('GEOMETRY_INVALID')
        polygon.append((round(finite(pair[0],0,1)*(w-1)),round(finite(pair[1],0,1)*(h-1))))
    allowed=np.zeros((h,w),np.uint8)
    cv2.fillPoly(allowed,[np.asarray(polygon,np.int32)],255)
    mask=cv2.inRange(cv2.cvtColor(frame,cv2.COLOR_BGR2HSV),np.array(lower,np.uint8),np.array(upper,np.uint8))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    # Even small or fragmented marked exclusion areas remain forbidden.
    forbidden=mask.copy(); accepted=0
    for contour in contours:
        area=cv2.contourArea(contour)
        if area < h*w*min_boundary_fraction:
            if len(contour)>=3:cv2.fillPoly(forbidden,[cv2.convexHull(contour)],255)
            continue
        hull=cv2.convexHull(contour)
        hull_area=cv2.contourArea(hull)
        if area<=0 or hull_area/area>1.6: return ()
        x,y,cw,ch=cv2.boundingRect(contour)
        if x<=1 or y<=1 or x+cw>=w-1 or y+ch>=h-1: return ()
        cv2.fillPoly(forbidden,[hull],255); accepted+=1
    if accepted==0: return ()
    kernel=np.ones((2*margin_pixels+1,2*margin_pixels+1),np.uint8)
    forbidden=cv2.dilate(forbidden,kernel)
    allowed=cv2.erode(allowed,kernel)
    result=[]
    for p in candidates:
        if type(p) is not Point: raise DeploymentError('TARGET_INVALID')
        x,y=round(p.x*(w-1)),round(p.y*(h-1))
        if allowed[y,x] and not forbidden[y,x]: result.append(p)
    return tuple(result)
