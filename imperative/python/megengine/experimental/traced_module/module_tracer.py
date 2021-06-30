# -*- coding: utf-8 -*-
# MegEngine is Licensed under the Apache License, Version 2.0 (the "License")
#
# Copyright (c) 2014-2021 Megvii Inc. All rights reserved.
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT ARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
import collections

from ... import Tensor
from ... import functional as F
from ...core.tensor.array_method import ArrayMethodMixin
from ...module import Module

_active_module_tracer = None


def active_module_tracer():
    return _active_module_tracer


def set_active_module_tracer(tracer):
    global _active_module_tracer
    _active_module_tracer = tracer


class module_tracer:

    # builtin types
    _opaque_types = set()

    _active_scopes = None

    def __init__(self, wrap_fn):
        self._active_scopes = []
        self.patcher = Patcher(wrap_fn)

    @classmethod
    def register_as_builtin(cls, mod):
        assert issubclass(mod, Module)
        cls._opaque_types.add(mod)
        return mod

    @classmethod
    def is_builtin(cls, mod):
        return type(mod) in cls._opaque_types

    def push_scope(self, scope):
        self._active_scopes.append(scope)

    def pop_scope(self):
        self._active_scopes.pop()

    def current_scope(self):
        if self._active_scopes:
            return self._active_scopes[-1]
        return None


class PatchedFn:
    frame_dict = None
    name = None
    origin_fn = None

    def __init__(self, frame_dict, name):
        self.frame_dict = frame_dict
        self.name = name
        self.origin_fn = (
            self.frame_dict[name]
            if isinstance(frame_dict, collections.abc.Mapping)
            else getattr(frame_dict, name)
        )

    def set_func(self, func):
        if isinstance(self.frame_dict, collections.abc.Mapping):
            self.frame_dict[self.name] = func
        else:
            setattr(self.frame_dict, self.name, func)


class Patcher:

    patched_fn_ids = set()
    _builtin_functions = []
    _builtin_modules = [
        F,
        F.distributed,
        F.elemwise,
        F.inplace,
        F.loss,
        F.math,
        F.metric,
        F.nn,
        F.quantized,
        F.tensor,
        F.utils,
        F.vision,
    ]
    _builtin_methods = [
        Tensor,
        ArrayMethodMixin,
    ]

    def __init__(self, wrap_fn):
        self.patched_fn = []
        self.visited_frames_ids = set()
        self.wrap_fn = wrap_fn
        for module in self._builtin_modules:
            self.patch_module(module)

        for cls in self._builtin_methods:
            self.patch_cls(cls)

        for i, j in self._builtin_functions:
            if id(i) not in self.visited_frames_ids:
                self.patch_function(i, j, self.wrap_fn)

    def patch_function(self, frame_dict, fn, wrap_fn):
        patched_fn = PatchedFn(frame_dict, fn)
        self.patched_fn_ids.add(id(patched_fn.origin_fn))
        patched_fn.set_func(wrap_fn(patched_fn.origin_fn))
        self.patched_fn.append(patched_fn)

    def patch_method(self, cls, name, wrap_fn):
        self.patch_function(cls, name, wrap_fn)

    def patch_cls(self, cls):
        import inspect

        if id(cls) not in self.visited_frames_ids:
            for k, v in cls.__dict__.items():
                if inspect.isfunction(v) and not k.startswith("_"):
                    self.patch_function(cls, k, self.wrap_fn)
            self.visited_frames_ids.add(id(cls))

    def patch_module(self, module):
        import inspect

        if id(module.__dict__) not in self.visited_frames_ids:
            for k, v in module.__dict__.items():
                if inspect.isfunction(v) and not k.startswith("_"):
                    self.patch_function(module.__dict__, k, self.wrap_fn)
            self.visited_frames_ids.add(id(module.__dict__))

    def auto_patch(self, frame_dict):
        if id(frame_dict) not in self.visited_frames_ids:
            for k, v in frame_dict.items():
                if id(v) in self.patched_fn_ids:
                    self.patch_function(frame_dict, k, self.wrap_fn)
        self.visited_frames_ids.add(id(frame_dict))

    def __enter__(self):
        return self

    def __exit__(self, type, vlaue, trace):
        while self.patched_fn:
            pf = self.patched_fn.pop()
            pf.set_func(pf.origin_fn)
        self.visited_frames_ids.clear()
