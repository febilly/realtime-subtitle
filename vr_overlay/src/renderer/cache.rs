use std::collections::{HashMap, VecDeque};
use std::hash::Hash;

use super::font_resolver::TextStyleKey;
use super::types::{BlockBounds, LayoutCacheKey, LineRole, TextStyleDescriptor, VisualBounds};

// ponytail: minimal LRU for DWrite text format reuse.  If throughput requires more
// concurrency replace with a sharded cache or dashmap.
#[derive(Debug)]
pub(crate) struct BoundedLruCache<K, V> {
    capacity: usize,
    entries: HashMap<K, V>,
    recency: VecDeque<K>,
}

impl<K, V> BoundedLruCache<K, V>
where
    K: Clone + Eq + Hash,
{
    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            capacity,
            entries: HashMap::with_capacity(capacity),
            recency: VecDeque::with_capacity(capacity),
        }
    }

    pub fn get(&mut self, key: &K) -> Option<&V> {
        if !self.entries.contains_key(key) {
            return None;
        }
        self.recency.retain(|c| c != key);
        self.recency.push_back(key.clone());
        self.entries.get(key)
    }

    pub fn insert(&mut self, key: K, value: V) {
        if self.capacity == 0 {
            return;
        }
        if self.entries.contains_key(&key) {
            self.entries.insert(key.clone(), value);
            self.recency.retain(|c| c != &key);
            self.recency.push_back(key);
            return;
        }
        while self.entries.len() >= self.capacity {
            if let Some(oldest) = self.recency.pop_front() {
                self.entries.remove(&oldest);
            } else {
                break;
            }
        }
        self.recency.push_back(key.clone());
        self.entries.insert(key, value);
    }

    pub fn contains_key(&self, key: &K) -> bool {
        self.entries.contains_key(key)
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }
}

// ── Layout cache types ──

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct CachedLineLayoutTemplate {
    pub text: String,
    pub role: LineRole,
    pub style_key: TextStyleKey,
    pub style: TextStyleDescriptor,
    pub width_px: f32,
    pub origin_x: f32,
    pub origin_y: f32,
    pub font_size_px: f32,
    pub visual_bounds: VisualBounds,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct CachedBlockLayoutTemplate {
    pub primary_lines: Vec<CachedLineLayoutTemplate>,
    pub secondary_line: Option<CachedLineLayoutTemplate>,
    pub secondary_reserved: bool,
    pub bounds: BlockBounds,
    pub visual_bounds: VisualBounds,
    pub content_width_px: f32,
    pub truncated_primary: bool,
    pub truncated_secondary: bool,
}

pub(crate) type LayoutCache = BoundedLruCache<LayoutCacheKey, CachedBlockLayoutTemplate>;

// ── Render command-list cache types (D3D11 side) ──

#[cfg(windows)]
use windows::Win32::Graphics::Direct2D::ID2D1CommandList;

#[cfg(windows)]
#[derive(Debug, Clone)]
pub(crate) struct CachedLineVisual {
    pub command_list: ID2D1CommandList,
    pub visual_bounds: VisualBounds,
}

#[cfg(windows)]
#[derive(Debug, Clone)]
pub(crate) struct CachedBlockVisual {
    pub command_list: ID2D1CommandList,
    pub visual_bounds: VisualBounds,
}

// ── Aggregated render caches for WindowsCaptionRenderer ──

#[cfg(windows)]
use super::types::{BlockCacheKey, LineCacheKey};

#[cfg(windows)]
use windows::Win32::Graphics::DirectWrite::IDWriteTextFormat;

#[cfg(windows)]
pub(crate) struct RenderCaches {
    pub layout_cache: LayoutCache,
    pub line_cache: BoundedLruCache<LineCacheKey, CachedLineVisual>,
    pub block_cache: BoundedLruCache<BlockCacheKey, CachedBlockVisual>,
    pub text_format_cache: BoundedLruCache<(TextStyleKey, u32), IDWriteTextFormat>,
}

#[cfg(windows)]
impl RenderCaches {
    pub fn new(text_format_cap: usize) -> Self {
        Self {
            layout_cache: LayoutCache::with_capacity(256),
            line_cache: BoundedLruCache::with_capacity(256),
            block_cache: BoundedLruCache::with_capacity(64),
            text_format_cache: BoundedLruCache::with_capacity(text_format_cap),
        }
    }
}
