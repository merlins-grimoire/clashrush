"""Closed private calibration loader and concrete memory-only observation port.

There is intentionally no automatically admitted production profile. A donor
filename is a candidate identity, not proof of a current HUD/control/target.
The exact calibrated pack must be bound to Run by its SHA-256 and reviewed tree.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import traceback
from dataclasses import dataclass
from collections.abc import Mapping

import cv2
import numpy as np

from .mvp_deployment import (
    Card, CardKind, CardState, Spell, Page, Observation, Screen, Point, Target,
    TargetKind, Intervention, DeploymentError, Policy, finite, CompiledDeploymentPlan,
    DeploymentDisposition, SourceInventoryEntry,
)
from .mvp_deployment_vision import (
    image, roi, CardGeometry, discover_card_boxes, classify_icon, matches,
    match_unique, read_quantity, exact_grey, candidate_ring, validated_ground, _normal_glyph, _gray,
    _quantity_binary,
)


PROFILE_NAME='private/deployment/profile.json'
_HEX=re.compile(r'[0-9a-f]{64}')
_KEY=re.compile(r'[a-z0-9][a-z0-9_.-]{0,63}')


class FrozenMap(Mapping):
    """Tuple-backed mapping: no mutable dictionary remains authoritative."""
    __slots__=('items_tuple',)
    def __init__(self,items): object.__setattr__(self,'items_tuple',tuple(items))
    def __setattr__(self,_name,_value): raise TypeError('frozen mapping')
    def __getitem__(self,key):
        for candidate,value in self.items_tuple:
            if candidate==key:return value
        raise KeyError(key)
    def __iter__(self): return (key for key,_ in self.items_tuple)
    def __len__(self): return len(self.items_tuple)


def _freeze(value):
    if type(value) is dict:return FrozenMap((key,_freeze(item)) for key,item in value.items())
    if type(value) is list:return tuple(_freeze(item) for item in value)
    if type(value) in (str,int,float,bool) or value is None:return value
    raise DeploymentError('PROFILE_SCHEMA')


@dataclass(frozen=True,slots=True)
class _Flags:
    writeable: bool=False


@dataclass(frozen=True,slots=True)
class ImmutableTemplate:
    """Authority-bearing pixels stored only as immutable bytes and scalars."""
    payload: bytes
    shape: tuple[int,...]
    flags: _Flags=_Flags()
    @classmethod
    def from_array(cls,value):
        array=np.ascontiguousarray(image(value,gray=True))
        return cls(array.tobytes(),tuple(int(v) for v in array.shape))
    def array(self):
        return np.frombuffer(self.payload,dtype=np.uint8).reshape(self.shape)
    def setflags(self,**_):
        raise ValueError('immutable template authority')
    def __array__(self,dtype=None,copy=None):
        array=self.array()
        if dtype is not None:array=array.astype(dtype,copy=True)
        elif copy is True:array=array.copy()
        return array


def _template(value):
    return value.array() if type(value) is ImmutableTemplate else value


def _source_absent(region,assets,keys,maximum_pixel_delta):
    # Whole-template alternatives, never a pixelwise union or an area average.
    for key in keys:
        template=_template(assets[key])
        if (region.shape==template.shape
                and int(cv2.absdiff(region,template).max())<=maximum_pixel_delta
                and match_unique(region,template,.93) is not None):
            return True
    return False


def _exact(mapping, keys):
    if type(mapping) is not dict or set(mapping)!=set(keys):
        raise DeploymentError('PROFILE_SCHEMA')


def _unique(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise DeploymentError('PROFILE_SCHEMA')
        out[k]=v
    return out


def _no_link(path):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
        raise DeploymentError('PROFILE_REDIRECTED')
    return info


def read_private(project: Path, relative: str, maximum: int) -> bytes:
    """Bounded read with no-follow opened paths; no configuration writes.

    POSIX walks with dir_fd/O_NOFOLLOW. Windows retains non-delete-shared
    ancestor handles, opens the file itself with OPEN_REPARSE_POINT, then checks
    its attributes before bytes are read. All outward failures are sanitized.
    """
    if type(relative) is not str or not re.fullmatch(r'private/deployment/(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+',relative):
        raise DeploymentError('PROFILE_PATH')
    if type(maximum) is not int or not 1<=maximum<=32*1024*1024:
        raise DeploymentError('PROFILE_SIZE')
    pieces=relative.split('/')
    if any(x in ('.','..') for x in pieces): raise DeploymentError('PROFILE_PATH')
    project=Path(project).absolute()
    try:
        if os.name=='nt': return _read_windows(project,pieces,maximum)
        descriptors=[]
        try:
            fd=os.open(project.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            descriptors.append(fd)
            for component in list(project.parts[1:])+pieces[:-1]:
                fd=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                descriptors.append(fd)
            file_fd=os.open(pieces[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
            descriptors.append(file_fd)
            info=os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>maximum:
                raise DeploymentError('PROFILE_SIZE')
            chunks=[]; total=0
            while True:
                data=os.read(file_fd,min(65536,maximum+1-total))
                if not data: break
                chunks.append(data); total+=len(data)
                if total>maximum: raise DeploymentError('PROFILE_SIZE')
            after=os.fstat(file_fd)
            if (info.st_ino,info.st_size,info.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
                raise DeploymentError('PROFILE_CHANGED')
            return b''.join(chunks)
        finally:
            closed=True
            for fd in reversed(descriptors):
                try:os.close(fd)
                except OSError:closed=False
            if not closed:raise DeploymentError('PROFILE_CLOSE_UNPROVED')
    except DeploymentError: raise
    except BaseException: raise DeploymentError('PROFILE_UNAVAILABLE') from None


def _read_windows(project, pieces, maximum):
    import ctypes
    from ctypes import wintypes as w
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype=w.HANDLE
    kernel.CloseHandle.argtypes=[w.HANDLE]; kernel.CloseHandle.restype=w.BOOL
    kernel.GetFileInformationByHandleEx.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
    kernel.GetFileInformationByHandleEx.restype=w.BOOL
    kernel.GetFileSizeEx.argtypes=[w.HANDLE,ctypes.POINTER(ctypes.c_longlong)]
    kernel.GetFileSizeEx.restype=w.BOOL
    kernel.ReadFile.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD),ctypes.c_void_p]
    kernel.ReadFile.restype=w.BOOL
    class Attributes(ctypes.Structure): _fields_=[('attributes',w.DWORD),('tag',w.DWORD)]
    opened=[]
    try:
        # Reject redirected ancestors all the way from the volume root.
        current=Path(project.anchor)
        components=project.parts[1:]+tuple(pieces)
        for index,part in enumerate(components):
            current=current/part
            directory=index<len(components)-1
            flags=0x00200000|(0x02000000 if directory else 0) # OPEN_REPARSE_POINT/BACKUP_SEMANTICS
            handle=kernel.CreateFileW(str(current),0x80000000,1,None,3,flags,None)
            if handle in (None,ctypes.c_void_p(-1).value): raise DeploymentError('PROFILE_UNAVAILABLE')
            opened.append(handle)
            attributes=Attributes()
            if not kernel.GetFileInformationByHandleEx(handle,9,ctypes.byref(attributes),ctypes.sizeof(attributes)):
                raise DeploymentError('PROFILE_UNAVAILABLE')
            if attributes.attributes&0x400 or bool(attributes.attributes&0x10)!=directory:
                raise DeploymentError('PROFILE_REDIRECTED')
        size=ctypes.c_longlong()
        if not kernel.GetFileSizeEx(opened[-1],ctypes.byref(size)) or not 0<=size.value<=maximum:
            raise DeploymentError('PROFILE_SIZE')
        buffer=ctypes.create_string_buffer(size.value+1); count=w.DWORD()
        if not kernel.ReadFile(opened[-1],buffer,size.value+1,ctypes.byref(count),None) or count.value!=size.value:
            raise DeploymentError('PROFILE_CHANGED')
        return buffer.raw[:count.value]
    finally:
        ok=True
        for handle in reversed(opened):
            if not kernel.CloseHandle(handle): ok=False
        if not ok: raise DeploymentError('PROFILE_CLOSE_UNPROVED')


def _validate_locator(value, assets):
    _exact(value,('template','roi'))
    if value['template'] not in assets: raise DeploymentError('PROFILE_ASSET_MISSING')
    box=value['roi']
    if type(box) is not list or len(box)!=4: raise DeploymentError('PROFILE_SCHEMA')
    a,b,c,d=(finite(v,0,1) for v in box)
    if not a<c or not b<d: raise DeploymentError('PROFILE_SCHEMA')


class DeploymentProfile:
    """Validated, deeply immutable configuration and byte-backed templates."""
    __slots__=('data','assets','digest','plan','_sealed')
    def __setattr__(self,name,value):
        if getattr(self,'_sealed',False): raise TypeError('sealed deployment profile')
        object.__setattr__(self,name,value)
    def __init__(self,data,assets,digest):
        # Validate detached mutable working values, then discard them completely.
        detached=json.loads(json.dumps(data,allow_nan=False))
        decoded={k:np.array(v,copy=True) for k,v in assets.items()}
        for value in decoded.values():
            image(value,gray=True)
            if float(value.std())<1:raise DeploymentError('PROFILE_ASSET_INVALID')
        self.data=detached; self.assets=decoded
        self.digest=digest
        self.validate()
        frozen_data=_freeze(detached)
        frozen_assets=FrozenMap((key,ImmutableTemplate.from_array(value))
                                for key,value in decoded.items())
        self.data=frozen_data; self.assets=frozen_assets
        self._sealed=True
    def validate(self):
        d=self.data
        if type(self.digest) is not str or not _HEX.fullmatch(self.digest):raise DeploymentError('PROFILE_SCHEMA')
        base_keys=('schema','profile_id','reviewed_tree','runtime_reviewed','size','assets','screens','controls',
                   'bar','cards','plan','glyphs','targets','ground','view_anchors','blockers')
        if set(d) not in (set(base_keys), set(base_keys) | {'source_inventory','dispositions'},
                          set(base_keys) | {'source_inventory','dispositions','source_layout'}):
            raise DeploymentError('PROFILE_SCHEMA')
        if type(d['schema']) is not int or d['schema'] not in (2,3) or type(d['runtime_reviewed']) is not bool:
            raise DeploymentError('PROFILE_SCHEMA')
        if (d['schema']==3) != ('source_layout' in d):
            raise DeploymentError('PROFILE_SCHEMA')
        if type(d['profile_id']) is not str or not _KEY.fullmatch(d['profile_id']): raise DeploymentError('PROFILE_SCHEMA')
        if type(d['reviewed_tree']) is not str or not re.fullmatch(r'[0-9a-f]{40}',d['reviewed_tree']): raise DeploymentError('PROFILE_SCHEMA')
        if type(d['size']) is not list or len(d['size'])!=2 or any(type(n) is not int for n in d['size']):
            raise DeploymentError('PROFILE_SCHEMA')
        w,h=d['size']
        if not(640<=w<=4096 and 360<=h<=2160): raise DeploymentError('PROFILE_SCHEMA')
        if set(d['assets'])!=set(self.assets) or not 1<=len(self.assets)<=256: raise DeploymentError('PROFILE_SCHEMA')
        for k,entry in d['assets'].items():
            _exact(entry,('file','sha256'))
            if not _KEY.fullmatch(k) or not re.fullmatch(r'[a-zA-Z0-9_.-]+\.png',entry['file']) or not _HEX.fullmatch(entry['sha256']):
                raise DeploymentError('PROFILE_SCHEMA')
        required={s.value for s in Screen if s is not Screen.UNKNOWN}
        if (type(d['screens']) is not dict
                or set(d['screens']) != required):
            raise DeploymentError('PROFILE_SCHEMA')
        for screen,rules in d['screens'].items():
            if type(rules) is not list or not 2<=len(rules)<=8: raise DeploymentError('PROFILE_SCHEMA')
            for rule in rules: _validate_locator(rule,self.assets)
            if len({r['template'] for r in rules})!=len(rules): raise DeploymentError('PROFILE_SCHEMA')
        required_controls={'HOME':{'attack'},'MATCH':{'find_match'},'ARMY':{'army_attack'},
                           'BATTLE':{'end_battle'},'END_CONFIRM':{'confirm_end'},'RESULT':{'return_home'}}
        expected_controls = required_controls
        if set(d['controls'])!=set(expected_controls): raise DeploymentError('PROFILE_SCHEMA')
        for screen,verbs in expected_controls.items():
            if set(d['controls'][screen])!=verbs: raise DeploymentError('PROFILE_SCHEMA')
            for rule in d['controls'][screen].values(): _validate_locator(rule,self.assets)
        navigation=[d['controls'][s][v] for s,v in
                    (('HOME','attack'),('MATCH','find_match'),('ARMY','army_attack'))]
        for i,left in enumerate(navigation if d['schema']==3 else ()):
            if any(np.array_equal(_gray(self.assets[left['template']]),
                                  _gray(self.assets[right['template']])) for right in navigation[i+1:]):
                raise DeploymentError('PROFILE_CONTROL_ALIAS')
        b=d['bar']; _exact(b,('roi','left_edge','right_edge','frame','geometry'))
        # Validate bar rectangle through the same bounded locator grammar.
        _validate_locator({'template':next(iter(self.assets)),'roi':b['roi']},self.assets)
        for edge in (b['left_edge'],b['right_edge']):
            _exact(edge,('end','continues'))
            _validate_locator(edge['end'],self.assets); _validate_locator(edge['continues'],self.assets)
        if type(b['frame']) is not list or len(b['frame'])!=4: raise DeploymentError('PROFILE_SCHEMA')
        for locator in b['frame']:_validate_locator(locator,self.assets)
        _exact(b['geometry'],('card_width','width_tolerance','peak_height','peak_distance'))
        CardGeometry(**b['geometry'])
        if type(d['cards']) is not list or not 1<=len(d['cards'])<=48: raise DeploymentError('PROFILE_SCHEMA')
        seen=set()
        for c in d['cards']:
            _exact(c,('identity','kind','spell','capacity','icons','icon_roi','count_roi','available','depleted','deployed','selected','state_rois'))
            if not _KEY.fullmatch(c['identity']) or c['identity'] in seen: raise DeploymentError('PROFILE_SCHEMA')
            seen.add(c['identity'])
            kind,spell=CardKind(c['kind']),Spell(c['spell'])
            if kind is CardKind.UNKNOWN or spell is Spell.UNKNOWN: raise DeploymentError('PROFILE_SCHEMA')
            if (kind is CardKind.SPELL)==(spell is Spell.NONE): raise DeploymentError('PROFILE_SCHEMA')
            if type(c['capacity']) is not int or not 1<=c['capacity']<=999: raise DeploymentError('PROFILE_SCHEMA')
            if kind in (CardKind.HERO,CardKind.CLAN) and c['capacity']!=1: raise DeploymentError('PROFILE_SCHEMA')
            for name in ('icons','available','depleted','deployed','selected'):
                values=c[name]
                if type(values) is not list or len(values)>8 or any(k not in self.assets for k in values):
                    raise DeploymentError('PROFILE_ASSET_MISSING')
            if not c['icons'] or not c['selected'] or not c['depleted']:
                raise DeploymentError('PROFILE_ASSET_MISSING')
            if kind in (CardKind.HERO,CardKind.CLAN) and (not c['available'] or not c['deployed']):
                raise DeploymentError('PROFILE_ASSET_MISSING')
            for name in ('icon_roi','count_roi'):
                _validate_locator({'template':c['icons'][0],'roi':c[name]},self.assets)
            if type(c['state_rois']) is not dict or set(c['state_rois'])!={'available','depleted','deployed','selected'}:
                raise DeploymentError('PROFILE_SCHEMA')
            for name,box in c['state_rois'].items():
                marker=(c[name] or c['icons']) [0]
                _validate_locator({'template':marker,'roi':box},self.assets)
            semantic=[(name,key) for name in ('available','depleted','deployed','selected')
                      for key in c[name]]
            for index,(name,key) in enumerate(semantic):
                if any(name!=other and np.array_equal(self.assets[key],self.assets[other_key])
                       for other,other_key in semantic[index+1:]):
                    raise DeploymentError('PROFILE_SCHEMA')
        if len({tuple(c['icon_roi']) for c in d['cards']})!=1 or len({tuple(c['count_roi']) for c in d['cards']})!=1:
            raise DeploymentError('PROFILE_SCHEMA')
        plan=d['plan']; _exact(plan,('layout','viewport_limit','roster'))
        if plan['layout']!='SINGLE_PAGE' or type(plan['roster']) is not list:
            raise DeploymentError('PROFILE_SCHEMA')
        definitions={c['identity']:c for c in d['cards']}
        compiled=[]
        for entry in plan['roster']:
            _exact(entry,('identity','initial_quantity'))
            identity=entry['identity']
            if identity not in definitions:raise DeploymentError('PROFILE_SCHEMA')
            spec=definitions[identity]; quantity=entry['initial_quantity']
            if type(quantity) is not int or not 1<=quantity<=spec['capacity']:
                raise DeploymentError('PROFILE_SCHEMA')
            compiled.append((identity,CardKind(spec['kind']),Spell(spec['spell']),quantity))
        source_inventory=()
        if 'source_inventory' in d:
            if type(d['source_inventory']) is not list or not 1<=len(d['source_inventory'])<=48:
                raise DeploymentError('PROFILE_SCHEMA')
            values=[]
            for entry in d['source_inventory']:
                _exact(entry,('identity','category','kind','spell','quantity'))
                values.append(SourceInventoryEntry(entry['identity'],entry['category'],
                                                   CardKind(entry['kind']),Spell(entry['spell']),
                                                   entry['quantity'],CardState.AVAILABLE if d['schema']==3 else None))
            source_inventory=tuple(values)
        dispositions=()
        if 'dispositions' in d:
            if type(d['dispositions']) is not list:
                raise DeploymentError('PROFILE_SCHEMA')
            items=[]
            for entry in d['dispositions']:
                _exact(entry,('identity','disposition'))
                items.append((entry['identity'],DeploymentDisposition(entry['disposition'])))
            dispositions=tuple(items)
        self.plan=CompiledDeploymentPlan(tuple(compiled),plan['viewport_limit'],
                                         dispositions=dispositions,
                                         source_inventory=source_inventory)
        if d['schema']==3:
            self._validate_source()
            layout=d['source_layout']
            assets_digest=hashlib.sha256(b''.join(
                key.encode()+np.ascontiguousarray(image(self.assets[key],gray=True)).tobytes()
                for key in sorted(self.assets))).hexdigest()
            self.plan=CompiledDeploymentPlan(tuple(compiled),plan['viewport_limit'],
                dispositions=dispositions,source_inventory=source_inventory,
                source_categories=tuple((c['category'],c['state']) for c in layout['categories']),
                source_context=(self.digest,d['reviewed_tree'],assets_digest,layout['build'],layout['mode']),
                match_entry_cost=layout['match']['expected_cost'],
                allow_empty_support=layout.get('allow_empty_support',False))
        if set(d['glyphs'])!=set('x0123456789') or any(k not in self.assets for k in d['glyphs'].values()):
            raise DeploymentError('PROFILE_ASSET_MISSING')
        normalized=[_normal_glyph((_gray(self.assets[d['glyphs'][key]])>=160).astype(np.uint8)*255)
                    for key in 'x0123456789']
        if any(np.array_equal(left,right) for index,left in enumerate(normalized)
               for right in normalized[index+1:]):raise DeploymentError('PROFILE_SCHEMA')
        if type(d['targets']) is not list or len(d['targets'])>32: raise DeploymentError('PROFILE_SCHEMA')
        for rule in d['targets']:
            _exact(rule,('kind','locator','required_context','excluded_context','context_radius'))
            kind=TargetKind(rule['kind'])
            if kind is TargetKind.GROUND: raise DeploymentError('PROFILE_SCHEMA')
            _validate_locator(rule['locator'],self.assets)
            finite(rule['context_radius'],.001,.15)
            for name in ('required_context','excluded_context'):
                if type(rule[name]) is not list or len(rule[name])>8: raise DeploymentError('PROFILE_SCHEMA')
                for ctx in rule[name]: _validate_locator(ctx,self.assets)
            if kind in (TargetKind.FRIENDLY_COHORT,TargetKind.INJURED_COHORT) and (not rule['required_context'] or not rule['excluded_context']):
                raise DeploymentError('PROFILE_SCHEMA')
        g=d['ground']; _exact(g,('marker','overlay_hsv','playfield','margin_pixels'))
        _validate_locator(g['marker'],self.assets)
        if type(g['overlay_hsv']) is not list or len(g['overlay_hsv'])!=2: raise DeploymentError('PROFILE_SCHEMA')
        if type(g['playfield']) is not list or not 3<=len(g['playfield'])<=16: raise DeploymentError('PROFILE_SCHEMA')
        if type(g['margin_pixels']) is not int or not 1<=g['margin_pixels']<=128: raise DeploymentError('PROFILE_SCHEMA')
        if type(d['view_anchors']) is not list or not 3<=len(d['view_anchors'])<=8: raise DeploymentError('PROFILE_SCHEMA')
        for rule in d['view_anchors']: _validate_locator(rule,self.assets)
        if type(d['blockers']) is not list or not 2<=len(d['blockers'])<=32:raise DeploymentError('PROFILE_SCHEMA')
        for rule in d['blockers']:_validate_locator(rule,self.assets)
        for p in g['playfield']:
            if type(p) is not list or len(p)!=2:raise DeploymentError('PROFILE_SCHEMA')
            for v in p:finite(v,0,1)
        for bound in g['overlay_hsv']:
            if type(bound) is not list or len(bound)!=3:raise DeploymentError('PROFILE_SCHEMA')
            for v,hi in zip(bound,(179,255,255)):
                if type(v) is not int or not 0<=v<=hi:raise DeploymentError('PROFILE_SCHEMA')

    def _validate_source(self):
        d=self.data; layout=d['source_layout']
        # The Battle witness grammar requires one identity owner for every
        # icon/state occurrence. Reject impossible packs before match entry.
        for index,left in enumerate(d['cards']):
            left_keys=[k for role in ('icons','available','depleted','deployed','selected') for k in left[role]]
            for right in d['cards'][index+1:]:
                right_keys=[k for role in ('icons','available','depleted','deployed','selected') for k in right[role]]
                if any(np.array_equal(_gray(self.assets[a]),_gray(self.assets[b]))
                       for a in left_keys for b in right_keys):
                    raise DeploymentError('PROFILE_AMBIGUOUS')
        keys=('build','mode','context','match','categories','entries','empty','available','selected','vacant',
              'icon_roi','count_roi','state_roi')
        _exact(layout,keys+tuple(k for k in ('quantity','allow_empty_support') if k in layout))
        if type(layout.get('allow_empty_support',False)) is not bool:
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        if 'quantity' in layout:
            quantity=layout['quantity']
            _exact(quantity,('threshold','minimum_component_area','glyphs'))
            if (type(quantity['threshold']) is not int or not 160<=quantity['threshold']<255
                    or type(quantity['minimum_component_area']) is not int
                    or not 1<=quantity['minimum_component_area']<=8):
                raise DeploymentError('PROFILE_SOURCE_INVALID')
            _exact(quantity['glyphs'],'x0123456789')
            normalized=[]
            for symbol,variants in quantity['glyphs'].items():
                if (type(variants) is not list or not 1<=len(variants)<=8
                        or any(type(key) is not str or key not in self.assets for key in variants)):
                    raise DeploymentError('PROFILE_ASSET_MISSING')
                for key in variants:
                    glyph=_normal_glyph(_quantity_binary(self.assets[key],quantity['threshold'],
                                                        quantity['minimum_component_area']))
                    if any(other!=symbol and np.array_equal(glyph,previous)
                           for other,previous in normalized):raise DeploymentError('PROFILE_AMBIGUOUS')
                    normalized.append((symbol,glyph))
        _exact(layout['context'],('source',))
        match=layout['match']
        _exact(match,('normal','ranked','cost','expected_cost'))
        if type(match['expected_cost']) is not int or not 1<=match['expected_cost']<=1000000:
            raise DeploymentError('PROFILE_MATCH_INVALID')
        for role in ('normal','ranked','cost'):_validate_locator(match[role],self.assets)
        match_art=[_gray(self.assets[match[role]['template']]) for role in ('normal','ranked','cost')]
        if any(np.array_equal(a,b) for i,a in enumerate(match_art) for b in match_art[i+1:]):
            raise DeploymentError('PROFILE_MATCH_INVALID')
        box=match['cost']['roi']; w,h=d['size']
        if match_art[2].shape!=(int(box[3]*h)-int(box[1]*h),int(box[2]*w)-int(box[0]*w)):
            raise DeploymentError('PROFILE_MATCH_INVALID')
        for locator in layout['context'].values():_validate_locator(locator,self.assets)
        context_art=[_gray(self.assets[r['template']]) for r in layout['context'].values()]
        if any(np.array_equal(a,b) for i,a in enumerate(context_art) for b in context_art[i+1:]):
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        if (type(layout['build']) is not str or not _KEY.fullmatch(layout['build'])
                or layout['mode']!='REGULAR_MULTIPLAYER'
                or 'ARMY' not in d['screens'] or 'ARMY' not in d['controls']):
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        categories=layout['categories']
        if (type(categories) is not list or
                [c.get('category') for c in categories]!=['ordinary','hero','spell','clan','siege']):
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        geometry=any('geometry' in c for c in categories)
        if geometry and not all('geometry' in c for c in categories):
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        for c in categories:
            keys=('category','state','header','status','complete','end','continues','slots')
            _exact(c,keys+tuple(k for k in ('empty_status','geometry') if k in c))
            if 'empty_status' in c:
                if c['category'] not in ('ordinary','hero','spell') or c['state']!='POPULATED':
                    raise DeploymentError('PROFILE_SOURCE_INVALID')
                _validate_locator(c['empty_status'],self.assets)
                if np.array_equal(self.assets[c['status']['template']],self.assets[c['empty_status']['template']]):
                    raise DeploymentError('PROFILE_SOURCE_INVALID')
            if c['state'] not in ('POPULATED','EMPTY','LOCKED'):
                raise DeploymentError('PROFILE_SOURCE_INVALID')
            for name in ('header','status','complete','end','continues'):
                _validate_locator(c[name],self.assets)
            if type(c['slots']) is not list or not (0 if geometry else 1)<=len(c['slots'])<=12:
                raise DeploymentError('PROFILE_SOURCE_INVALID')
            if geometry:self._validate_source_geometry(c)
            for index,box in enumerate(c['slots']):
                _validate_locator({'template':layout['empty'],'roi':box},self.assets)
                fields=c['geometry']['fields'][index] if geometry else layout
                vacant=self.assets.get(fields['vacant'])
                w,h=d['size']
                if vacant is None or vacant.shape[:2]!=(int(box[3]*h)-int(box[1]*h),int(box[2]*w)-int(box[0]*w)):
                    raise DeploymentError('PROFILE_SOURCE_INVALID')
        for name in ('empty','available','selected'):
            if layout[name] not in self.assets:raise DeploymentError('PROFILE_ASSET_MISSING')
        for name in ('icon_roi','count_roi','state_roi'):
            _validate_locator({'template':layout['empty'],'roi':layout[name]},self.assets)
        if type(layout['entries']) is not list or not 1<=len(layout['entries'])<=48:
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        for entry in layout['entries']:
            _exact(entry,('identity','category','kind','spell','capacity','icons'))
            SourceInventoryEntry(entry['identity'],entry['category'],CardKind(entry['kind']),
                                 Spell(entry['spell']),entry['capacity'])
            if (type(entry['icons']) is not list or not entry['icons'] or len(entry['icons'])>8
                    or any(key not in self.assets for key in entry['icons'])):
                raise DeploymentError('PROFILE_ASSET_MISSING')
        expected={e.identity:e for e in self.plan.source_inventory}
        if (len({e['identity'] for e in layout['entries']})!=len(layout['entries'])
                or {e['identity'] for e in layout['entries']}!=set(expected)):
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        for entry in layout['entries']:
            e=expected[entry['identity']]
            if ((entry['category'],entry['kind'],entry['spell'])!=(e.category,e.kind.value,e.spell.value)
                    or e.quantity>entry['capacity']):raise DeploymentError('PROFILE_SOURCE_INVALID')
        for index,left in enumerate(layout['entries']):
            for right in layout['entries'][index+1:]:
                if left['category']==right['category'] and any(
                        np.array_equal(_gray(self.assets[a]),_gray(self.assets[b]))
                        for a in left['icons'] for b in right['icons']):
                    raise DeploymentError('PROFILE_AMBIGUOUS')
        slots=[box for c in categories for box in c['slots']]
        for i,a in enumerate(slots):
            for b in slots[i+1:]:
                if max(a[0],b[0])<min(a[2],b[2]) and max(a[1],b[1])<min(a[3],b[3]):
                    raise DeploymentError('PROFILE_SOURCE_INVALID')
        states=[self.assets[layout[name]] for name in ('empty','available','selected')]
        if any(np.array_equal(a,b) for i,a in enumerate(states) for b in states[i+1:]):
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        if geometry:
            extents=[c['geometry']['extent'] for c in categories]
            for i,a in enumerate(extents):
                if any(max(a[0],b[0])<min(a[2],b[2]) and max(a[1],b[1])<min(a[3],b[3])
                       for b in extents[i+1:]):raise DeploymentError('PROFILE_SOURCE_INVALID')

    def _validate_source_geometry(self,category):
        """Complete category partition; every non-card pixel has positive proof."""
        geometry=category['geometry']
        _exact(geometry,('extent','fields','background')+
               tuple(k for k in ('maximum_pixel_delta',) if k in geometry))
        delta=geometry.get('maximum_pixel_delta',0)
        if type(delta) is not int or not 0<=delta<=16:
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        marker=self.data['source_layout']['empty']; w,h=self.data['size']
        def rectangle(box):
            _validate_locator({'template':marker,'roi':box},self.assets)
            a,b,c,d=(int(v*s) for v,s in zip(box,(w,h,w,h)))
            if a>=c or b>=d:raise DeploymentError('PROFILE_SOURCE_INVALID')
            return a,b,c,d
        extent=rectangle(geometry['extent'])
        fields=geometry['fields']; background=geometry['background']
        if (type(fields) is not list or len(fields)!=len(category['slots'])
                or type(background) is not list or len(background)>32):
            raise DeploymentError('PROFILE_SOURCE_INVALID')
        def absence(primary,alternatives,box):
            if type(alternatives) is not list or len(alternatives)>7:
                raise DeploymentError('PROFILE_SOURCE_INVALID')
            for key in [primary]+alternatives:
                if type(key) is not str or key not in self.assets:
                    raise DeploymentError('PROFILE_ASSET_MISSING')
                if self.assets[key].shape!=(box[3]-box[1],box[2]-box[0],3):
                    raise DeploymentError('PROFILE_SOURCE_INVALID')
        for field,slot in zip(fields,category['slots']):
            _exact(field,('icon_roi','count_roi','state_roi','empty','available','selected','vacant')+
                   tuple(k for k in ('vacant_alternatives',) if k in field))
            for name in ('empty','available','selected','vacant'):
                if type(field[name]) is not str or field[name] not in self.assets:
                    raise DeploymentError('PROFILE_ASSET_MISSING')
            for name in ('icon_roi','count_roi','state_roi'):
                _validate_locator({'template':marker,'roi':field[name]},self.assets)
            states=[self.assets[field[name]] for name in ('empty','available','selected')]
            if any(np.array_equal(a,b) for i,a in enumerate(states) for b in states[i+1:]):
                raise DeploymentError('PROFILE_SOURCE_INVALID')
            absence(field['vacant'],field.get('vacant_alternatives',[]),rectangle(slot))
        boxes=[rectangle(box) for box in category['slots']]
        for rule in background:
            _exact(rule,('template','roi')+tuple(k for k in ('alternatives',) if k in rule))
            _validate_locator({k:rule[k] for k in ('template','roi')},self.assets)
            box=rectangle(rule['roi'])
            absence(rule['template'],rule.get('alternatives',[]),box)
            boxes.append(box)
        for i,(a,b,c,d) in enumerate(boxes):
            if not (extent[0]<=a<c<=extent[2] and extent[1]<=b<d<=extent[3]):
                raise DeploymentError('PROFILE_SOURCE_INVALID')
            if any(max(a,e)<min(c,g) and max(b,f)<min(d,j) for e,f,g,j in boxes[i+1:]):
                raise DeploymentError('PROFILE_SOURCE_INVALID')
        if sum((c-a)*(d-b) for a,b,c,d in boxes)!=(extent[2]-extent[0])*(extent[3]-extent[1]):
            raise DeploymentError('PROFILE_SOURCE_INVALID')


def load_profile(project: Path, *, candidate_tree: str, runtime: bool=True) -> DeploymentProfile:
    raw=read_private(project,PROFILE_NAME,256*1024)
    try:
        data=json.loads(raw,object_pairs_hook=_unique)
        if json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()+b'\n' != raw:
            raise DeploymentError('PROFILE_NONCANONICAL')
        if data.get('reviewed_tree')!=candidate_tree or (runtime and data.get('runtime_reviewed') is not True):
            raise DeploymentError('PROFILE_NOT_ADMITTED')
        if runtime and data.get('schema')!=3:
            raise DeploymentError('SOURCE_PROFILE_REQUIRED')
        if type(data.get('assets')) is not dict or not 1<=len(data['assets'])<=256:raise DeploymentError('PROFILE_SCHEMA')
        assets={}; total=0
        for name,entry in data['assets'].items():
            _exact(entry,('file','sha256'))
            filename=entry['file']
            if type(filename) is not str or not re.fullmatch(r'[a-zA-Z0-9_.-]+\.png',filename):
                raise DeploymentError('PROFILE_PATH')
            payload=read_private(project,'private/deployment/templates/'+filename,2*1024*1024)
            total+=len(payload)
            if total>32*1024*1024 or hashlib.sha256(payload).hexdigest()!=entry['sha256']:
                raise DeploymentError('PROFILE_ASSET_MISMATCH')
            if payload[:8]!=b'\x89PNG\r\n\x1a\n' or payload[12:16]!=b'IHDR': raise DeploymentError('PROFILE_ASSET_INVALID')
            w,h=struct.unpack('>II',payload[16:24])
            if not 1<=w<=512 or not 1<=h<=512: raise DeploymentError('PROFILE_ASSET_INVALID')
            a=cv2.imdecode(np.frombuffer(payload,dtype=np.uint8),cv2.IMREAD_COLOR)
            image(a)
            if a.shape[:2]!=(h,w): raise DeploymentError('PROFILE_ASSET_INVALID')
            assets[name]=a
        return DeploymentProfile(data,assets,hashlib.sha256(raw).hexdigest())
    except DeploymentError: raise
    except BaseException: raise DeploymentError('PROFILE_INVALID') from None


@dataclass(frozen=True, slots=True)
class InstallationEvidence:
    """Read-only adapter output. Binding identity must be the selected owned instance.

    This value is not a trust source by itself. Only the composition root may
    supply the metadata reader; profile flags/artwork never create evidence.
    """
    binding: object
    package: str
    version: str


def require_installation(profile, binding, installed_build):
    """Validate optional diagnostics, never mint window/UI/account authority.

    Decision B removes the mandatory guest query. None means unavailable, not
    a successful package observation. Production still supplies its owned view
    and positively recognizes each fresh frame before any action.
    """
    if binding is None:
        raise DeploymentError('WINDOW_BINDING_UNCERTAIN')
    if installed_build is None:
        return None
    try:
        if not callable(installed_build):
            raise DeploymentError('PACKAGE_METADATA_CONFLICT')
        evidence=installed_build(binding)
        if evidence is None:
            return None
        valid=(type(evidence) is InstallationEvidence and evidence.binding is binding
               and type(evidence.package) is str and evidence.package=='com.supercell.clashofclans'
               and type(evidence.version) is str and evidence.version==profile.plan.source_context[3])
    except BaseException:
        valid=False
    if not valid:raise DeploymentError('PACKAGE_METADATA_CONFLICT')
    return evidence


class FrameObserver:
    """Concrete capture→semantic observation; only detached scalars escape."""
    def __init__(self, *, profile: DeploymentProfile, capture, monotonic, intervention, home_verified,
                 build_binding=None, installed_build=None):
        if type(profile) is not DeploymentProfile or not all(callable(c) for c in (capture,monotonic,intervention,home_verified)):
            raise DeploymentError('PORT_INVALID')
        self.profile=profile; self.capture=capture; self.clock=monotonic
        self.intervention=intervention; self.home_verified=home_verified
        self.sequence=0
        self.build_binding=build_binding; self.installed_build=installed_build
    def _locate(self,frame,rule,multiple=False):
        patch=roi(frame,rule['roi']); template=_template(self.profile.assets[rule['template']])
        found=matches(patch,template,.93,maximum=16) if multiple else ()
        if not multiple:
            one=match_unique(patch,template,.93)
            found=() if one is None else (one,)
        h,w=frame.shape[:2]; x0=int(rule['roi'][0]*w); y0=int(rule['roi'][1]*h)
        return tuple((Point((x0+x+tw/2)/(w-1),(y0+y+th/2)/(h-1)),score)
                     for x,y,tw,th,score in found)
    def _independent_occurrences(self,frame,rules):
        # Search ROIs may overlap. The actual matched pixels must not be
        # counted twice, even for unequal/nested or renamed templates.
        boxes=[]; h,w=frame.shape[:2]
        for rule in rules:
            found=self._locate(frame,rule)
            if len(found)!=1:raise DeploymentError('ROLE_OCCURRENCE_UNPROVED')
            point=found[0][0]
            th,tw=_template(self.profile.assets[rule['template']]).shape[:2]
            x,y=round(point.x*(w-1)-tw/2),round(point.y*(h-1)-th/2)
            box=(x,y,x+tw,y+th)
            if any(max(x,a)<min(x+tw,c) and max(y,b)<min(y+th,e)
                   for a,b,c,e in boxes):
                raise DeploymentError('ROLE_OCCURRENCE_ALIASED')
            boxes.append(box)

    def _any(self,card,keys,box):
        patch=roi(card,box)
        return any(match_unique(patch,_template(self.profile.assets[k]),.93) is not None for k in keys)
    def _state(self,card,spec,name):
        if not spec[name]:return False
        catalog={state:tuple(_template(self.profile.assets[key]) for key in spec[state])
                 for state in ('available','depleted','deployed','selected') if spec[state]}
        result=classify_icon(roi(card,spec['state_rois'][name]),catalog,.93,.04)
        if result is not None and result!=name:raise DeploymentError('CARD_STATE_CONFLICT')
        return result==name
    @staticmethod
    def _owns(point,box,relative):
        x,y=point; left,top,right,bottom=box; width=right-left; height=bottom-top
        return (left+relative[0]*width<=x<left+relative[2]*width
                and top+relative[1]*height<=y<top+relative[3]*height)
    def _page(self,frame):
        d=self.profile.data; b=d['bar']; crop=roi(frame,b['roi'])
        hypotheses=discover_card_boxes(crop,CardGeometry(**b['geometry']))
        definitions={c['identity']:c for c in d['cards']}
        catalog={c['identity']:tuple(_template(self.profile.assets[k]) for k in c['icons']) for c in d['cards']}
        candidates=[]
        for box in hypotheses:
            x0,y0,x1,y1=box; region=crop[y0:y1,x0:x1]
            structural=[self._any(region,(rule['template'],),rule['roi']) for rule in b['frame']]
            if any(structural) and not all(structural): raise DeploymentError('BAR_RESIDUE')
            if not all(structural):
                candidates.append((box,None))
                continue
            icon_box=d['cards'][0]['icon_roi']
            identity=classify_icon(roi(region,icon_box),catalog,.93,.04)
            if identity is None: raise DeploymentError('UNKNOWN_CARD_PRESENT')
            candidates.append((box,identity))
        occupied=[item for item in candidates if item[1] is not None]
        occupied.sort(key=lambda item:item[0][0])
        if not occupied: raise DeploymentError('BAR_UNPROVED')
        if any(a[0][2]>z[0][0] for a,z in zip(occupied,occupied[1:])):
            raise DeploymentError('BAR_AMBIGUOUS')
        # An unframed width-compatible hypothesis is interior artwork only when
        # both boundaries are strictly owned by one admitted complete card.
        # Touching/equal boundaries, partial overlap, continuation and bridges
        # remain alternate complete/clipped-card explanations and fail closed.
        for box,identity in candidates:
            if identity is not None: continue
            left,_,right,_=box
            owners=[owner for owner,_ in occupied
                    if owner[0] < left < right < owner[2]]
            if len(owners)!=1: raise DeploymentError('BAR_RESIDUE')
        signatures=tuple((identity,CardKind(definitions[identity]['kind']),
                          Spell(definitions[identity]['spell'])) for _,identity in occupied)
        if len(set(signatures))!=len(signatures): raise DeploymentError('ROSTER_DUPLICATE')
        if len(signatures)>self.profile.plan.viewport_limit: raise DeploymentError('PLAN_VIEWPORT_OVERFLOW')
        if signatures!=self.profile.plan.signatures:
            if any(item not in self.profile.plan.signatures for item in signatures):
                raise DeploymentError('ROSTER_ADDITIONAL')
            raise DeploymentError('ROSTER_INCOMPLETE')
        endpoint_roles=tuple(
            (side,state,b[side][state])
            for side in ('left_edge','right_edge')
            for state in ('end','continues'))
        role_counts={(side,state):0 for side,state,_ in endpoint_roles}
        frame_h,frame_w=frame.shape[:2]
        occurrences={}
        for key in dict.fromkeys(rule['template'] for _,_,rule in endpoint_roles):
            for x,y,tw,th,_ in matches(
                    frame,_template(self.profile.assets[key]),.93,maximum=32):
                occurrences.setdefault((x,y,tw,th),set()).add(key)
        for (x,y,tw,th),keys in occurrences.items():
            owners=[]
            for side,state,rule in endpoint_roles:
                if rule['template'] not in keys: continue
                left,top,right,bottom=rule['roi']
                if (x>=left*frame_w and y>=top*frame_h
                        and x+tw<=right*frame_w and y+th<=bottom*frame_h):
                    owners.append((side,state))
            if len(owners)!=1: raise DeploymentError('UNEXPLAINED_WITNESS')
            role_counts[owners[0]]+=1
            if role_counts[owners[0]]>1:
                raise DeploymentError('BAR_ENDPOINT_UNPROVED')
        def endpoint(side):
            end=role_counts[(side,'end')]==1
            more=role_counts[(side,'continues')]==1
            if end==more: raise DeploymentError('BAR_ENDPOINT_UNPROVED')
            return end
        left_edge=endpoint('left_edge'); right_edge=endpoint('right_edge')
        if not left_edge or not right_edge: raise DeploymentError('SINGLE_PAGE_COVERAGE_UNPROVED')
        # Every structural, identity, state and quantity template occurrence in
        # the whole declared viewport must have exactly one compatible owner.
        witness_rules=[]
        for rule in b['frame']:
            witness_rules.append((rule['template'],rule['roi'],None))
        for identity,spec in definitions.items():
            for key in spec['icons']:witness_rules.append((key,spec['icon_roi'],identity))
            for state in ('available','depleted','deployed','selected'):
                for key in spec[state]:witness_rules.append((key,spec['state_rois'][state],identity))
        for key in d['glyphs'].values():witness_rules.append((key,d['cards'][0]['count_roi'],None))
        for key,relative,identity in witness_rules:
            for x,y,tw,th,_ in matches(crop,_template(self.profile.assets[key]),.93,maximum=32):
                center=(x+tw/2,y+th/2)
                owners=[1 for box,owner in occupied
                        if (identity is None or owner==identity) and self._owns(center,box,relative)]
                if len(owners)!=1: raise DeploymentError('UNEXPLAINED_WITNESS')
        cards=[]; h,w=frame.shape[:2]
        bank={k:_template(self.profile.assets[v]) for k,v in d['glyphs'].items()}
        for (x0,y0,x1,y1),identity in occupied:
            region=crop[y0:y1,x0:x1]; spec=definitions[identity]
            kind,spell=CardKind(spec['kind']),Spell(spec['spell'])
            depleted=self._state(region,spec,'depleted')
            deployed=self._state(region,spec,'deployed')
            available=self._state(region,spec,'available')
            selected=self._state(region,spec,'selected')
            if (depleted and available) or (deployed and available): raise DeploymentError('CARD_STATE_CONFLICT')
            if kind in (CardKind.HERO,CardKind.CLAN):
                if deployed: q=0; state=CardState.DEPLOYED
                elif depleted: q=0; state=CardState.DEPLETED
                elif available: q=1; state=CardState.AVAILABLE
                else: raise DeploymentError('CARD_STATE_UNKNOWN')
            else:
                q=read_quantity(roi(region,spec['count_roi']),bank,spec['capacity'])
                if q==0:
                    if not depleted or exact_grey(roi(region,spec['icon_roi'])) is not True:
                        raise DeploymentError('DEPLETION_UNPROVED')
                    state=CardState.DEPLETED
                else:
                    if depleted: raise DeploymentError('CARD_STATE_CONFLICT')
                    state=CardState.AVAILABLE
            point=Point((int(b['roi'][0]*w)+(x0+x1)/2)/(w-1),
                        (int(b['roi'][1]*h)+(y0+y1)/2)/(h-1))
            cards.append(Card(identity,kind,spell,q,state,point,selected))
        return Page(tuple(cards),True,True)
    def _targets(self,frame):
        d=self.profile.data; targets=[]
        # Three independently calibrated view anchors must remain visible.
        if not all(self._locate(frame,r) for r in d['view_anchors']): return ()
        g=d['ground']
        if self._locate(frame,g['marker']):
            points=validated_ground(frame,candidate_ring(frame),overlay_hsv=g['overlay_hsv'],
                                    playfield=g['playfield'],margin_pixels=g['margin_pixels'])
            targets.extend(Target(TargetKind.GROUND,p,1.0) for p in points)
        for rule in d['targets']:
            for point,score in self._locate(frame,rule['locator'],True):
                radius=rule['context_radius']
                def nearby(ctx):
                    return any(point.near(p,radius) for p,_ in self._locate(frame,ctx,True))
                if all(nearby(ctx) for ctx in rule['required_context']) and not any(nearby(ctx) for ctx in rule['excluded_context']):
                    targets.append(Target(TargetKind(rule['kind']),point,score))
        return tuple(targets)
    def _source(self,frame):
        if self.profile.data['schema']!=3:
            raise DeploymentError('SOURCE_PROFILE_REQUIRED')
        layout=self.profile.data['source_layout']; entries=[]; categories=[]; witnesses=[]
        h,w=frame.shape[:2]
        def own(key,box,relative=(0,0,1,1)):
            left,top,right,bottom=box
            witnesses.append((key,(int(left*w),int(top*h),int(right*w),int(bottom*h)),relative))
        self._independent_occurrences(frame,tuple(layout['context'].values())+
            tuple(self.profile.data['screens']['ARMY'])+
            (self.profile.data['controls']['ARMY']['army_attack'],))
        geometry='geometry' in layout['categories'][0]
        global_rules=tuple(layout['context'].values())
        if geometry:
            global_rules+=tuple(self.profile.data['screens']['ARMY'])+(self.profile.data['controls']['ARMY']['army_attack'],)
        for rule in global_rules:
            own(rule['template'],rule['roi'])
        quantity=layout.get('quantity')
        glyphs=quantity['glyphs'] if quantity is not None else {
            k:(v,) for k,v in self.profile.data['glyphs'].items()}
        bank={k:tuple(_template(self.profile.assets[v]) for v in variants)
              for k,variants in glyphs.items()}
        quantity_policy={} if quantity is None else {
            name:quantity[name] for name in ('threshold','minimum_component_area')}
        glyph_keys={key for variants in glyphs.values() for key in variants}
        semantic_keys=set()
        for category in layout['categories']:
            if geometry:
                delta=category['geometry'].get('maximum_pixel_delta',0)
                for rule in category['geometry']['background']:
                    if not _source_absent(roi(frame,rule['roi']),self.profile.assets,
                            (rule['template'],)+tuple(rule.get('alternatives',())),delta):
                        raise DeploymentError('SOURCE_EXTENT_UNPROVED')
            for role in ('header','status','complete','end','continues'):
                own(category[role]['template'],category[role]['roi'])
            state=category['state']
            positive=bool(self._locate(frame,category['status']))
            empty_status='empty_status' in category and bool(self._locate(frame,category['empty_status']))
            if 'empty_status' in category:own(category['empty_status']['template'],category['empty_status']['roi'])
            if positive==empty_status:raise DeploymentError('SOURCE_STATE_UNPROVED')
            if empty_status:state='EMPTY'
            if (not all(self._locate(frame,category[role]) for role in ('header','complete','end'))
                    or self._locate(frame,category['continues'])):
                raise DeploymentError('SOURCE_EXTENT_UNPROVED')
            definitions={e['identity']:e for e in layout['entries'] if e['category']==category['category']}
            catalog={key:tuple(_template(self.profile.assets[k]) for k in spec['icons'])
                     for key,spec in definitions.items()}
            count=0
            for index,box in enumerate(category['slots']):
                fields=category['geometry']['fields'][index] if geometry else layout
                semantic_keys.update(fields[name] for name in ('empty','available','selected'))
                region=roi(frame,box)
                empty=self._any(region,(fields['empty'],),fields['icon_roi'])
                identity=classify_icon(roi(region,fields['icon_roi']),catalog,.93,.04) if catalog else None
                if empty:
                    vacant=(_source_absent(region,self.profile.assets,
                            (fields['vacant'],)+tuple(fields.get('vacant_alternatives',())),delta)
                            if geometry else match_unique(region,_template(self.profile.assets[fields['vacant']]),.93) is not None)
                    if not vacant:
                        raise DeploymentError('SOURCE_VACANCY_UNPROVED')
                    if identity is not None or self._any(region,(fields['available'],fields['selected']),fields['state_roi']):
                        raise DeploymentError('SOURCE_AMBIGUOUS')
                    own(fields['empty'],box,fields['icon_roi'])
                    continue
                if identity is None or state!='POPULATED':
                    raise DeploymentError('SOURCE_UNKNOWN_ENTRY')
                if (not self._any(region,(fields['available'],),fields['state_roi'])
                        or self._any(region,(fields['selected'],),fields['state_roi'])):
                    raise DeploymentError('SOURCE_STATE_UNPROVED')
                spec=definitions[identity]
                for key in spec['icons']:own(key,box,fields['icon_roi'])
                own(fields['available'],box,fields['state_roi'])
                if CardKind(spec['kind']) is CardKind.HERO:
                    q=1  # Available portrait is a singleton; its level is never a count.
                else:
                    if not geometry:
                        for key in glyph_keys:own(key,box,fields['count_roi'])
                    q=read_quantity(roi(region,fields['count_roi']),bank,spec['capacity'],**quantity_policy)
                entries.append(SourceInventoryEntry(identity,category['category'],CardKind(spec['kind']),
                                                    Spell(spec['spell']),q,CardState.AVAILABLE))
                count+=1
            if (count>0)!=(state=='POPULATED'):
                raise DeploymentError('SOURCE_CATEGORY_UNPROVED')
            categories.append((category['category'],state))
        if len({e.identity for e in entries})!=len(entries):
            raise DeploymentError('SOURCE_DUPLICATE')
        keys=semantic_keys
        if not geometry:keys.update(glyph_keys)
        keys.update(key for e in layout['entries'] for key in e['icons'])
        # A digit is not a globally unique control: headers, levels and timers
        # legitimately repeat it. Explicit geometry instead proves every whole
        # count field and every non-card background; semantic witnesses remain
        # globally owned. Legacy profiles keep their original global alphabet.
        # Numeric membership cannot remove a semantic role or grant it a count
        # field owner. Explicit geometry never adds numeric owners above.
        keys.update(key for key,_,_ in witnesses)
        for key in keys:
            for x,y,tw,th,_ in matches(frame,_template(self.profile.assets[key]),.93,maximum=64):
                owners=[1 for k,box,relative in witnesses if key==k
                        and self._owns((x,y),box,relative)
                        and self._owns((x+tw-1,y+th-1),box,relative)]
                if len(owners)!=1:raise DeploymentError('SOURCE_UNEXPLAINED_WITNESS')
        return tuple(entries),tuple(categories)
    def __call__(self):
        frame=None; result=None; failed=False
        start=finite(self.clock())
        try:
            if self.profile.data['schema']==3:
                require_installation(self.profile,self.build_binding,self.installed_build)
            frame=self.capture()
            image(frame)
            w,h=self.profile.data['size']
            if frame.shape!=(h,w,3) or not frame.flags.writeable: raise DeploymentError('FRAME_GEOMETRY')
            blocked=any(self._locate(frame,r) for r in self.profile.data['blockers'])
            state_matches=[] if blocked else [Screen(name) for name,rules in self.profile.data['screens'].items()
                           if all(self._locate(frame,r) for r in rules)]
            if len(state_matches)>1: raise DeploymentError('SCREEN_AMBIGUOUS')
            screen=state_matches[0] if state_matches else Screen.UNKNOWN
            page=self._page(frame) if screen is Screen.BATTLE else None
            controls=[]
            for name,rule in self.profile.data['controls'].get(screen.value,{}).items():
                found=self._locate(frame,rule)
                if found: controls.append((name,found[0][0]))
            if self.profile.data['schema']==3 and screen in (Screen.HOME,Screen.MATCH):
                verb='attack' if screen is Screen.HOME else 'find_match'
                self._independent_occurrences(frame,tuple(self.profile.data['screens'][screen.value])+
                    (self.profile.data['controls'][screen.value][verb],))
                # A preparatory button may not be the committing green control.
                final=self.profile.data['controls']['ARMY']['army_attack']
                hits=self._locate(frame,final,True)
                if hits:
                    self._independent_occurrences(frame,(
                        self.profile.data['controls'][screen.value][verb],final))
            match_mode=''; match_cost=None
            if self.profile.data['schema']==3 and screen is Screen.MATCH:
                match=self.profile.data['source_layout']['match']
                self._independent_occurrences(frame,(match['normal'],match['cost'],
                    self.profile.data['controls']['MATCH']['find_match']))
                # Ranked artwork may coexist. It must not own any of the
                # positively calibrated normal-choice/fee/Find occurrences.
                if self._locate(frame,match['ranked'],True):
                    self._independent_occurrences(frame,(match['normal'],match['cost'],
                        self.profile.data['controls']['MATCH']['find_match'],match['ranked']))
                # Whole fee field including currency, not a matching substring.
                if not np.array_equal(_gray(roi(frame,match['cost']['roi'])),
                                      _gray(_template(self.profile.assets[match['cost']['template']]))):
                    raise DeploymentError('MATCH_COST_UNPROVED')
                match_mode='REGULAR_MULTIPLAYER'; match_cost=match['expected_cost']
            home=self.home_verified(frame) if screen is Screen.HOME else False
            if type(home) is not bool: raise DeploymentError('HOME_UNPROVED')
            view=self.profile.digest
            if screen is Screen.BATTLE:
                anchor_points=[]
                for rule in self.profile.data['view_anchors']:
                    found=self._locate(frame,rule)
                    if len(found)!=1:raise DeploymentError('VIEW_UNPROVED')
                    p=found[0][0]
                    anchor_points.extend((round(p.x*(w-1)),round(p.y*(h-1))))
                # Memory-only coordinate token; never part of a public receipt.
                view=self.profile.digest[:16]+':'+','.join(map(str,anchor_points))
            source_inventory,source_categories = self._source(frame) if screen is Screen.ARMY else ((),())
            result=Observation(self.sequence+1,start,view,screen,page,
                self._targets(frame) if screen is Screen.BATTLE else (),tuple(controls),
                self.intervention(),home,source_inventory,source_categories,
                self.profile.plan.source_context if screen is Screen.ARMY else (),match_mode,match_cost)
            self.sequence+=1
        except BaseException as error:
            traceback.clear_frames(error.__traceback__)
            failed=True
        finally:
            if type(frame) is np.ndarray and frame.flags.writeable: frame.fill(0)
            frame=None
        if failed: raise DeploymentError('FRAME_OBSERVATION_UNAVAILABLE')
        return result
