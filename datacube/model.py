# coding=utf-8
"""
Core classes used across modules.
"""
from __future__ import absolute_import, division
import logging
from collections import namedtuple
import numpy as np
from osgeo import osr

_LOG = logging.getLogger(__name__)

Range = namedtuple('Range', ('begin', 'end'))


# Match datasets. (for mapping to storage)
class DatasetMatcher(object):
    def __init__(self, metadata):
        # Match by exact metadata properties (a subset of the metadata doc)
        #: :type: dict
        self.metadata = metadata


class StorageType(object):
    def __init__(self, descriptor, id_=None):
        # A definition of the storage (understood by the storage driver)
        #: :type: dict
        self.descriptor = descriptor

    @property
    def driver(self):
        return self.descriptor['driver']

    @property
    def projection(self):
        return str(self.descriptor['crs']).strip()

    @property
    def spatial_dimensions(self):
        sr = osr.SpatialReference(self.projection)
        if sr.IsGeographic():
            return 'longitude', 'latitude'
        elif sr.IsProjected():
            return 'x', 'y'

    @property
    def tile_size(self):
        """
        :return: tuple(x size, y size)
        """
        tile_size = self.descriptor['tile_size']
        return [tile_size[dim] for dim in self.spatial_dimensions]

    @property
    def resolution(self):
        """
        :return: tuple(x res, y res)
        """
        res = self.descriptor['resolution']
        return [res[dim] for dim in self.spatial_dimensions]

    @property
    def chunking(self):
        chunks = self.descriptor['chunking']
        return [(dim, chunks[dim]) for dim in self.descriptor['dimension_order']]

    @property
    def filename_format(self):
        return self.descriptor['filename_format']


class StorageTypeDescriptor(object):
    def __init__(self, projection, tile_size, resolution, dimension_order, chunking, filename_format):
        self.projection = projection
        self.tile_size = tile_size
        self.resolution = resolution
        self.dimension_order = dimension_order
        self.chunking = chunking
        self.filename_format = filename_format


class StorageMapping(object):
    def __init__(self, storage_type, name, description,
                 match, measurements,
                 location, filename_pattern, roi, id_=None):
        # Which datasets to match.
        #: :type: DatasetMatcher
        self.match = match

        #: :type: StorageType
        self.storage_type = storage_type

        # A name for the mapping (specified by users). (unique to the storage type)
        #: :type: str
        self.name = name

        # A human-readable, potentially multi-line, description for display on the UI.
        #: :type: str
        self.description = description

        # A dictionary of the measurements to store
        # (key is measurement id, value is a doc understood by the storage driver)
        #: :type: dict
        self.measurements = measurements

        # ROI for this mapping
        #: :type: dict
        self.roi = roi

        # The location where the storage units should be stored.
        #: :type: str
        self.location = location

        # Storage Unit filename pattern
        # TODO: define pattern expansion rules
        #: :type: str
        self.filename_pattern = filename_pattern

        # Database primary key
        #: :type: int
        self.id_ = id_

    def local_path_to_location_offset(self, filepath):
        assert filepath.startswith(self.location)
        return filepath[len(self.location):]

    def resolve_location(self, offset):
        # We can't use urlparse.urljoin() because it takes a relative path, not a path inside the base.
        return '/'.join(s.strip('/') for s in (self.location, offset))

    @property
    def storage_pattern(self):
        return self.resolve_location(self.filename_pattern)

    def __repr__(self):
        return 'StorageMapping<name={!r}, id_={!r}>'.format(self.name, self.id_)


class StorageUnit(object):
    def __init__(self, dataset_ids, storage_mapping, descriptor, path, id_=None):
        #: :type: list[uuid.UUID]
        self.dataset_ids = dataset_ids

        #: :type: StorageMapping
        self.storage_mapping = storage_mapping

        # A descriptor for this segment. (parameters etc)
        # A 'document' understandable by the storage driver. Properties inside may be queried by users.
        #: :type: dict
        self.descriptor = descriptor

        # An offset from the location defined in the storage type.
        #: :type: pathlib.Path
        self.path = path

        # Database primary key
        #: :type: int
        self.id_ = id_

    @property
    def filepath(self):
        filepath = self.storage_mapping.resolve_location(self.path)
        assert filepath.startswith('file://')
        return filepath[7:]

    def __str__(self):
        return "StorageUnit <type={m.name}, path={path}>".format(path=self.path, m=self.storage_mapping)


class Dataset(object):
    def __init__(self, collection, metadata_doc, metadata_path):
        """
        A dataset on disk.

        :type collection: Collection
        :param metadata_doc: the document (typically a parsed json/yaml)
        :type metadata_doc: dict
        :param metadata_path:
        :type metadata_path: Path
        """
        super(Dataset, self).__init__()
        #: :type: Collection
        self.collection = collection
        #: :type: dict
        self.metadata_doc = metadata_doc
        #: :type: pathlib.Path
        self.metadata_path = metadata_path

    @property
    def id(self):
        return self.metadata_doc['id']

    @property
    def format(self):
        return self.metadata_doc['format']['name']

    def __str__(self):
        return "Dataset <id={id}, path={path}>".format(id=self.id, path=self.metadata_path)

    def __repr__(self):
        return self.__str__()

    @property
    def metadata(self):
        return self.collection.dataset_reader(self.metadata_doc)


class Collection(object):
    def __init__(self,
                 name,
                 description,
                 match,
                 dataset_offsets,
                 dataset_search_fields,
                 storage_unit_search_fields,
                 id_=None):
        """
        Collection of datasets & storage.
        """
        self.id_ = id_

        # Name of collection. Unique.
        self.name = name

        self.description = description

        # Match datasets that should belong to this collection.
        self.match = match

        #: :type: DatasetOffsets
        self.dataset_offsets = dataset_offsets

        #: :type: dict[str, datacube.index.fields.Field]
        self.dataset_fields = dataset_search_fields
        #: :type: dict[str, datacube.index.fields.Field]
        self.storage_fields = storage_unit_search_fields

    def dataset_reader(self, dataset_doc):
        return _DocReader(self.dataset_offsets.__dict__, dataset_doc)


class DatasetOffsets(object):
    """
    Where to find certain fields in dataset metadata.
    """

    def __init__(self,
                 uuid_field,
                 label_field,
                 creation_time_field,
                 measurements_dict,
                 sources):
        # UUID for a dataset. Always unique.
        #: :type: tuple[string]
        self.uuid_field = uuid_field

        # The dataset "label" is the logical identifier for a dataset.
        #
        # -> Multiple datasets may arrive with the same label, but only the 'latest' will be returned by default
        #    in searches.
        #
        # Use case: reprocessing a dataset.
        # -> When reprocessing a dataset, the new dataset should be produced with the same label as the old one.
        # -> Because you probably don't want both datasets returned from typical searches. (they are the same data)
        # -> When ingested, this reprocessed dataset will be the only one visible to typical searchers.
        # -> But the old dataset will still exist in the database for provenance & historical record.
        #       -> Existing higher-level/derived datasets will still link to the old dataset they were processed
        #          from, even if it's not the latest.
        #
        # An example label used by GA (called "dataset_ids" on historical systems):
        #      -> Eg. "LS7_ETM_SYS_P31_GALPGS01-002_114_73_20050107"
        #
        # But the collection owner can use any string to label their datasets.
        #: :type: tuple[string]
        self.label_field = label_field

        # datetime the dataset was processed/created.
        #: :type: tuple[string]
        self.creation_time_field = creation_time_field

        # Where to find a dict of measurements/bands in the dataset.
        #  -> Dict key is measurement/band id,
        #  -> Dict value is object with fields depending on the storage driver.
        #     (such as path to band file, offset within file etc.)
        #: :type: tuple[string]
        self.measurements_dict = measurements_dict

        # Where to find a dict of embedded source datasets
        #  -> The dict is of form: classifier->source_dataset_doc
        #  -> 'classifier' is how to classify/identify the relationship (usually the type of source it was eg. 'nbar').
        #      An arbitrary string, but you should be consistent between datasets (to query relationships).
        #: :type: tuple[string]
        self.sources = sources


class TileSpec(object):
    """
    Defines a single Storage Unit, its CRS, location, resolution, and global attributes

    >>> from affine import Affine
    >>> wgs84 = osr.SpatialReference()
    >>> r = wgs84.ImportFromEPSG(4326)
    >>> t = TileSpec(wgs84.ExportToWkt(), Affine(0.00025, 0.0, 151.0, 0.0, -0.00025, -29.0), 4000, 4000)
    >>> t.lat_min, t.lat_max
    (-30.0, -29.0)
    >>> t.lon_min, t.lon_max
    (151.0, 152.0)
    >>> t.lats
    array([-29.000125, -29.000375, -29.000625, ..., -29.999375, -29.999625,
           -29.999875])
    >>> t.lons
    array([ 151.000125,  151.000375,  151.000625, ...,  151.999375,
            151.999625,  151.999875])


    :param crs: WKT representation of the coordinate reference system
    :type crs: str
    :param affine: Affine transformation defining the location of the storage unit
    :type affine: affine.Affine
    :param global_attrs: Extra attributes to store in each storage unit
    :type global_attrs: dict
    """

    def __init__(self, crs, affine, height, width, global_attrs=None):
        self.projection = crs
        self.affine = affine
        self.global_attrs = global_attrs or {}
        self.height = height
        self.width = width

        self.extents = [(0, 0), (0, height), (width, height), (width, 0)]
        affine.itransform(self.extents)

        if not affine.is_rectilinear:
            raise RuntimeError("rotation and/or shear are not supported")

        xs = np.arange(width) * affine.a + affine.c + affine.a / 2
        ys = np.arange(height) * affine.e + affine.f + affine.e / 2

        self.crs = osr.SpatialReference(crs)
        if self.crs.IsGeographic():
            self.lons = xs
            self.lats = ys
        elif self.crs.IsProjected():
            self.xs = xs
            self.ys = ys

            wgs84 = osr.SpatialReference()
            wgs84.ImportFromEPSG(4326)
            transform = osr.CoordinateTransformation(self.crs, wgs84)
            self.extents = transform.TransformPoints(self.extents)

    @property
    def lat_min(self):
        return min(ll[1] for ll in self.extents)

    @property
    def lat_max(self):
        return max(ll[1] for ll in self.extents)

    @property
    def lon_min(self):
        return min(ll[0] for ll in self.extents)

    @property
    def lon_max(self):
        return max(ll[0] for ll in self.extents)

    @property
    def lat_res(self):
        return self.affine.e

    @property
    def lon_res(self):
        return self.affine.a

    def __repr__(self):
        return repr(self.__dict__)


def _get_doc_offset(offset, document):
    """
    :type offset: list[str]
    :type document: dict

    >>> _get_doc_offset(['a'], {'a': 4})
    4
    >>> _get_doc_offset(['a', 'b'], {'a': {'b': 4}})
    4
    >>> _get_doc_offset(['a'], {})
    Traceback (most recent call last):
    ...
    KeyError: 'a'
    """
    value = document
    for key in offset:
        value = value[key]
    return value


def _set_doc_offset(offset, document, value):
    """
    :type offset: list[str]
    :type document: dict

    >>> doc = {'a': 4}
    >>> _set_doc_offset(['a'], doc, 5)
    >>> doc
    {'a': 5}
    >>> doc = {'a': {'b': 4}}
    >>> _set_doc_offset(['a', 'b'], doc, 'c')
    >>> doc
    {'a': {'b': 'c'}}
    """
    read_offset = offset[:-1]
    sub_doc = _get_doc_offset(read_offset, document)
    sub_doc[offset[-1]] = value


class _DocReader(object):
    def __init__(self, field_offsets, doc):
        """
        :type field_offsets: dict[str,list[str]]
        :type doc: dict
        >>> d = _DocReader({'lat': ['extent', 'lat']}, doc={'extent': {'lat': 4}})
        >>> d.lat
        4
        >>> d.lat = 5
        >>> d._doc
        {'extent': {'lat': 5}}
        >>> d.lon
        Traceback (most recent call last):
        ...
        ValueError: Unknown field 'lon'. Expected one of ['lat']
        """
        self.__dict__['_field_offsets'] = field_offsets
        self.__dict__['_doc'] = doc

    def __getattr__(self, name):
        offset = self._field_offsets.get(name)
        if offset is None:
            raise ValueError(
                'Unknown field %r. Expected one of %r' % (
                    name, list(self._field_offsets.keys())
                )
            )
        return _get_doc_offset(offset, self._doc)

    def __setattr__(self, name, val):
        offset = self._field_offsets.get(name)
        if offset is None:
            raise ValueError(
                'Unknown field %r. Expected one of %r' % (
                    name, list(self._field_offsets.keys())
                )
            )
        return _set_doc_offset(offset, self._doc, val)