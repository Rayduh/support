#!/usr/bin/python
#
# Copyright (C) 2017 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Creates the EmojiCompat font with the metadata. Metadata is embedded in FlatBuffers binary format
under a meta tag with name 'Emji'.

In order to create the final font the followings are used as inputs:

- NotoColorEmoji.ttf: Emoji font in the Android framework. Currently at
external/noto-fonts/emoji/NotoColorEmoji.ttf

- Unicode files: Unicode files that are in the framework, and lists information about all the
emojis. These files are emoji-data.txt, emoji-sequences.txt and emoji-zwj-sequences.txt. Currently
at external/unicode/

- android-emoji-data.txt: Includes emojis that are not defined in Unicode files, but are in
the Android font.

- data/emoji_metadata.txt: The file that includes the id, codepoints, the first Android OS version
that the emoji was added (sdkAdded), and finally the first EmojiCompat font version that the emoji
was added (compatAdded). Updated when the script is executed.

- data/emoji_metadata.fbs: The flatbuffer schema file. See http://google.github.io/flatbuffers/.

After execution the following files are generated if they don't exist otherwise, they are updated:

- ../tests/assets/NotoColorEmojiCompat.ttf
- ../../bundled-typeface/assets/NotoColorEmojiCompat.ttf
- ../src/android/support/text/emoji/flatbuffer/*
- data/emoji_metadata.txt
"""

from __future__ import print_function

import contextlib
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from fontTools import ttLib

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))

# Font file to process
# Unicode file names to read emoji data
EMOJI_DATA_FILE = 'emoji-data.txt'
EMOJI_SEQ_FILE = 'emoji-sequences.txt'
EMOJI_ZWJ_FILE = 'emoji-zwj-sequences.txt'
# library specific input directory
INPUT_DIR = os.path.join(SCRIPT_DIR, "data")
# emojis that are not defined in unicode files
ANDROID_EMOJIS_FILE = os.path.join(INPUT_DIR, 'android-emoji-data.txt')
# emoji metadata file
INPUT_CSV_FILE = os.path.join(INPUT_DIR, 'emoji_metadata.txt')
# flatbuffer schema
FLATBUFFER_SCHEMA = os.path.join(INPUT_DIR, 'emoji_metadata.fbs')
# emoji metadata json output file
OUTPUT_CSV_FILE = os.path.join(INPUT_DIR, 'emoji_metadata.txt')
# name of the compat font file
NEW_FONT_NAME = 'NotoColorEmojiCompat.ttf'

# main directories where output files are created
EMOJI_CORE_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
BUNDLED_MODULE_DIR = os.path.abspath(
    os.path.join(SCRIPT_DIR, os.pardir, os.pardir, 'bundled-typeface'))
SUPPORT_ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir, os.pardir, os.pardir))

# remapped font output file under test directory
TEST_FONT_FILE_PATH = os.path.join(EMOJI_CORE_DIR, 'tests', 'assets', NEW_FONT_NAME)
BUNDLED_ASSET_DIR = os.path.join(BUNDLED_MODULE_DIR, 'assets')
BUNDLED_ASSET_PATH = os.path.join(BUNDLED_ASSET_DIR, NEW_FONT_NAME)
# emoji metadata json output file
OUTPUT_JSON_FILE_NAME = 'emoji_metadata.json'
# the temporary binary file generated by flatbuffer
FLATBUFFER_BIN = 'emoji_metadata.bin'
# directory representation for flatbuffer java package
FLATBUFFER_PACKAGE_PATH = os.path.join('android', 'support', 'text', 'emoji', 'flatbuffer', '')
# the directory that contains flatbuffer java files
FLATBUFFER_JAVA_PATH = FLATBUFFER_PACKAGE_PATH
# file path for java header, it will be prepended to flatbuffer java files
FLATBUFFER_HEADER = os.path.join(INPUT_DIR, "flatbuffer_header.txt")
FLATBUFFER_METADATA_LIST_JAVA = "MetadataList.java"
FLATBUFFER_METADATA_ITEM_JAVA = "MetadataItem.java"
# directory under source where flatbuffer java files will be generated in
FLATBUFFER_JAVA_TARGET = os.path.join(EMOJI_CORE_DIR, 'src', FLATBUFFER_PACKAGE_PATH)

# meta tag name used in the font to embed the emoji metadata. This value is also used in
# MetadataListReader.java in order to locate the metadata location.
EMOJI_META_TAG_NAME = 'Emji'
# Codepoints that are rendered with emoji style by default even though not defined so in
# <code>emoji-data.txt</code>. Defined in <code>fontchain_lint.py</code>.
EMOJI_STYLE_EXCEPTIONS = [0x2600, 0x2601, 0x260e, 0x261d, 0x263a, 0x2660,
                          0x2663, 0x2665, 0x2666, 0x270c, 0x2744, 0x2764]

DEFAULT_EMOJI_ID = 0xF0001
EMOJI_STYLE_VS = 0xFE0F
# Last Android SDK Version
SDK_VERSION = 25
# metadata version that will be embedded into font.
METADATA_VERSION = 1


def makedirs_if_not_exists(path):
    """Creates the directory if it does not exist"""
    if not os.path.isdir(path):
        os.makedirs(path)

def to_hex(value):
    return format(value, 'X')

def hex_str_to_int(string):
    """Convert a hex string into int"""
    return int(string, 16)

def codepoint_to_string(codepoints):
    """Converts a list of codepoints into a string separated with space."""
    return ' '.join([hex(x) for x in codepoints])


def prepend_header_to_file(file_path):
    """Prepends the header to the file. Used to update flatbuffer java files with header, comments
    and annotations."""
    with open(file_path, "r+") as original_file:
        with open(FLATBUFFER_HEADER, "r") as copyright_file:
            original_content = original_file.read()
            start_index = original_content.index("public final class")
            original_file.seek(0)
            original_file.write(copyright_file.read() + "\n" + original_content[start_index:])


def update_flatbuffer_java_files(flatbuffer_java_dir):
    """Prepends headers to flatbuffer java files and copies to the final destination"""
    tmp_metadata_list = flatbuffer_java_dir + FLATBUFFER_METADATA_LIST_JAVA
    tmp_metadata_item = flatbuffer_java_dir + FLATBUFFER_METADATA_ITEM_JAVA
    prepend_header_to_file(tmp_metadata_list)
    prepend_header_to_file(tmp_metadata_item)
    shutil.copy(tmp_metadata_list, FLATBUFFER_JAVA_TARGET + FLATBUFFER_METADATA_LIST_JAVA)
    shutil.copy(tmp_metadata_item, FLATBUFFER_JAVA_TARGET + FLATBUFFER_METADATA_ITEM_JAVA)


class _EmojiData(object):
    """Holds the information about a single emoji."""

    def __init__(self, codepoints, is_emoji_style):
        self.codepoints = codepoints
        self.emoji_style = is_emoji_style
        self.emoji_id = 0
        self.width = 0
        self.height = 0
        self.sdk_added = SDK_VERSION
        self.compat_added = METADATA_VERSION

    def update_metrics(self, metrics):
        """Updates width/height instance variables with the values given in metrics dictionary.
        :param metrics: a dictionary object that has width and height values.
        """
        self.width = metrics.width
        self.height = metrics.height

    def __repr__(self):
        return '<EmojiData {0} - {1}>'.format(self.emoji_style,
                                              codepoint_to_string(self.codepoints))

    def create_json_element(self):
        """Creates the json representation of EmojiData."""
        json_element = {}
        json_element['id'] = self.emoji_id
        json_element['emojiStyle'] = self.emoji_style
        json_element['sdkAdded'] = self.sdk_added
        json_element['compatAdded'] = self.compat_added
        json_element['width'] = self.width
        json_element['height'] = self.height
        json_element['codepoints'] = self.codepoints
        return json_element

    def create_txt_row(self):
        """Creates array of values for CSV of EmojiData."""
        row = [to_hex(self.emoji_id), self.sdk_added, self.compat_added]
        row += [to_hex(x) for x in self.codepoints]
        return row

    def update(self, emoji_id, sdk_added, compat_added):
        """Updates current EmojiData with the values in a json element"""
        self.emoji_id = emoji_id
        self.sdk_added = sdk_added
        self.compat_added = compat_added


def read_emoji_lines(file_path):
    """Read all lines in an unicode emoji file into a list of uppercase strings. Ignore the empty
    lines and comments
    :param file_path: unicode emoji file path
    :return: list of uppercase strings
    """
    result = []
    for line in open(file_path):
        line = line.strip()
        if line and not line.startswith('#'):
            result.append(line.upper())
    return result


def read_emoji_intervals(emoji_data_map, file_path):
    """Read unicode lines of unicode emoji file in which each line describes a set of codepoint
    intervals. Expands the interval on a line and inserts related EmojiDatas into emoji_data_map.
    A line format that is expected is as follows:
    1F93C..1F93E ; [Emoji|Emoji_Presentation|Emoji_Modifier_Base] # [...]"""
    lines = read_emoji_lines(file_path)

    for line in lines:
        codepoints_range, emoji_property = [x.strip() for x in line.split('#')[0].split(';')]
        is_emoji_style = emoji_property == 'EMOJI_PRESENTATION'
        codepoints = []
        if '..' in codepoints_range:
            range_start, range_end = codepoints_range.split('..')
            codepoints_range = range(hex_str_to_int(range_start),
                                     hex_str_to_int(range_end) + 1)
            codepoints.extend(codepoints_range)
        else:
            codepoints.append(hex_str_to_int(codepoints_range))

        for codepoint in codepoints:
            key = codepoint_to_string([codepoint])
            codepoint_is_emoji_style = is_emoji_style or codepoint in EMOJI_STYLE_EXCEPTIONS
            if key in emoji_data_map:
                # since there are multiple definitions of emojis, only update when emoji style is
                # True
                if codepoint_is_emoji_style:
                    emoji_data_map[key].emoji_style = True
            else:
                emoji_data = _EmojiData([codepoint], codepoint_is_emoji_style)
                emoji_data_map[key] = emoji_data


def read_emoji_sequences(emoji_data_map, file_path):
    """Reads the content of the file which contains emoji sequences. Creates EmojiData for each
    line and puts into emoji_data_map."""
    lines = read_emoji_lines(file_path)
    # 1F1E6 1F1E8 ; Name ; [...]
    for line in lines:
        codepoints = [hex_str_to_int(x) for x in line.split(';')[0].strip().split(' ')]
        codepoints = [x for x in codepoints if x != EMOJI_STYLE_VS]
        key = codepoint_to_string(codepoints)
        if not key in emoji_data_map:
            emoji_data = _EmojiData(codepoints, False)
            emoji_data_map[key] = emoji_data


def load_emoji_data_map(unicode_path):
    """Reads the emoji data files, constructs a map of space separated codepoints to EmojiData.
    :return: map of space separated codepoints to EmojiData
    """
    emoji_data_map = {}
    read_emoji_intervals(emoji_data_map, os.path.join(unicode_path, EMOJI_DATA_FILE))
    read_emoji_sequences(emoji_data_map, os.path.join(unicode_path, EMOJI_ZWJ_FILE))
    read_emoji_sequences(emoji_data_map, os.path.join(unicode_path, EMOJI_SEQ_FILE))

    # EMOJI_NEW_FILE is optional
    if os.path.isfile(ANDROID_EMOJIS_FILE):
        read_emoji_sequences(emoji_data_map, ANDROID_EMOJIS_FILE)

    return emoji_data_map


def load_previous_metadata(emoji_data_map):
    """Updates emoji data elements in emoji_data_map using the id, sdk_added and compat_added fields
       in emoji_metadata.txt. Returns the smallest available emoji id to use. i.e. if the largest
       emoji id emoji_metadata.txt is 1, function would return 2. If emoji_metadata.txt does not
       exist, or contains no emojis defined returns DEFAULT_EMOJI_ID"""
    current_emoji_id = DEFAULT_EMOJI_ID
    if os.path.isfile(INPUT_CSV_FILE):
        with open(INPUT_CSV_FILE) as csvfile:
            reader = csv.reader(csvfile, delimiter=' ')
            for row in reader:
                if row[0].startswith('#'):
                    continue
                emoji_id = hex_str_to_int(row[0])
                sdk_added = int(row[1])
                compat_added = int(row[2])
                key = codepoint_to_string(hex_str_to_int(x) for x in row[3:])
                if key in emoji_data_map:
                    emoji_data = emoji_data_map[key]
                    emoji_data.update(emoji_id, sdk_added, compat_added)
                    if emoji_data.emoji_id >= current_emoji_id:
                        current_emoji_id = emoji_data.emoji_id + 1

    return current_emoji_id


def update_ttlib_orig_sort():
    """Updates the ttLib tag sort with a closure that makes the meta table first."""
    orig_sort = ttLib.sortedTagList

    def meta_first_table_sort(tag_list, table_order=None):
        """Sorts the tables with the original ttLib sort, then makes the meta table first."""
        tag_list = orig_sort(tag_list, table_order)
        tag_list.remove('meta')
        tag_list.insert(0, 'meta')
        return tag_list

    ttLib.sortedTagList = meta_first_table_sort


def inject_meta_into_font(ttf, flatbuffer_bin_filename):
    """inject metadata binary into font"""
    if not 'meta' in ttf:
        ttf['meta'] = ttLib.getTableClass('meta')()
    meta = ttf['meta']
    with contextlib.closing(open(flatbuffer_bin_filename)) as flatbuffer_bin_file:
        meta.data[EMOJI_META_TAG_NAME] = flatbuffer_bin_file.read()

    # sort meta tables for faster access
    update_ttlib_orig_sort()


def validate_input_files(font_path, unicode_path):
    """Validate the existence of font file and the unicode files"""
    if not os.path.isfile(font_path):
        raise ValueError("Font file does not exist: " + font_path)

    if not os.path.isdir(unicode_path):
        raise ValueError(
            "Unicode directory does not exist or is not a directory " + unicode_path)

    emoji_filenames = [os.path.join(unicode_path, EMOJI_DATA_FILE),
                       os.path.join(unicode_path, EMOJI_ZWJ_FILE),
                       os.path.join(unicode_path, EMOJI_SEQ_FILE)]
    for emoji_filename in emoji_filenames:
        if not os.path.isfile(emoji_filename):
            raise ValueError("Unicode emoji data file does not exist: " + emoji_filename)


class EmojiFontCreator(object):
    """Creates the EmojiCompat font"""

    def __init__(self, font_path, unicode_path):
        validate_input_files(font_path, unicode_path)

        self.font_path = font_path
        self.unicode_path = unicode_path
        self.emoji_data_map = {}
        self.remapped_codepoints = {}
        self.glyph_to_image_metrics_map = {}
        # set default emoji id to start of Supplemental Private Use Area-A
        self.emoji_id = DEFAULT_EMOJI_ID

    def update_emoji_data(self, codepoints, glyph_name):
        """Updates the existing EmojiData identified with codepoints. The fields that are set are:
        - emoji_id (if it does not exist)
        - image width/height"""
        key = codepoint_to_string(codepoints)
        if key in self.emoji_data_map:
            # add emoji to final data
            emoji_data = self.emoji_data_map[key]
            emoji_data.update_metrics(self.glyph_to_image_metrics_map[glyph_name])
            if emoji_data.emoji_id == 0:
                emoji_data.emoji_id = self.emoji_id
                self.emoji_id = self.emoji_id + 1
            self.remapped_codepoints[emoji_data.emoji_id] = glyph_name

    def read_cbdt(self, ttf):
        """Read image size data from CBDT."""
        cbdt = ttf['CBDT']
        for strike_data in cbdt.strikeData:
            for key, data in strike_data.iteritems():
                data.decompile()
                self.glyph_to_image_metrics_map[key] = data.metrics

    def read_and_clear_cmap12(self, ttf, glyph_to_codepoint_map):
        """Reads single code point emojis that are in cmap12, updates glyph_to_codepoint_map and
        finally clears all elements in CMAP 12"""
        cmap = ttf['cmap']
        for table in cmap.tables:
            if table.format == 12 and table.platformID == 3 and table.platEncID == 10:
                for codepoint, glyph_name in table.cmap.iteritems():
                    glyph_to_codepoint_map[glyph_name] = codepoint
                    self.update_emoji_data([codepoint], glyph_name)
                # clear all existing entries
                table.cmap.clear()
                return table
        raise ValueError("Font doesn't contain cmap with format:12, platformID:3 and platEncID:10")

    def read_and_clear_gsub(self, ttf, glyph_to_codepoint_map):
        """Reads the emoji sequences defined in GSUB and clear all elements under GSUB"""
        gsub = ttf['GSUB']
        for lookup in gsub.table.LookupList.Lookup:
            for subtable in lookup.SubTable:
                for name, ligatures in subtable.ligatures.iteritems():
                    for ligature in ligatures:
                        glyph_names = [name] + ligature.Component
                        codepoints = [glyph_to_codepoint_map[x] for x in glyph_names]
                        self.update_emoji_data(codepoints, ligature.LigGlyph)
                # clear all ligatures
                subtable.ligatures.clear()

    def write_metadata_json(self, output_json_file_path):
        """Writes the emojis into a json file"""
        output_json = {}
        output_json['version'] = METADATA_VERSION
        output_json['list'] = []

        emoji_data_list = sorted(self.emoji_data_map.values(), key=lambda x: x.emoji_id)

        total_emoji_count = 0
        for emoji_data in emoji_data_list:
            element = emoji_data.create_json_element()
            output_json['list'].append(element)
            total_emoji_count = total_emoji_count + 1

        # write the new json file to be processed by FlatBuffers
        with open(output_json_file_path, 'w') as json_file:
            print(json.dumps(output_json, indent=4, sort_keys=True, separators=(',', ':')),
                  file=json_file)

        return total_emoji_count

    def write_metadata_csv(self):
        """Writes emoji metadata into space separated file"""
        with open(OUTPUT_CSV_FILE, 'w') as csvfile:
            csvwriter = csv.writer(csvfile, delimiter=' ')
            emoji_data_list = sorted(self.emoji_data_map.values(), key=lambda x: x.emoji_id)
            csvwriter.writerow(['#id', 'sdkAdded', 'compatAdded', 'codepoints'])
            for emoji_data in emoji_data_list:
                csvwriter.writerow(emoji_data.create_txt_row())

    def create_font(self):
        """Creates the EmojiCompat font.
        :param font_path: path to Android NotoColorEmoji font
        :param unicode_path: path to directory that contains unicode files
        """

        tmp_dir = tempfile.mkdtemp()

        # create emoji codepoints to EmojiData map
        self.emoji_data_map = load_emoji_data_map(self.unicode_path)

        # read previous metadata file to update id, sdkAdded and compatAdded. emoji id that is
        # returned is either default or 1 greater than the largest id in previous data
        self.emoji_id = load_previous_metadata(self.emoji_data_map)

        with contextlib.closing(ttLib.TTFont(self.font_path)) as ttf:
            # set the font revision to be the METADATA_VERSION
            ttf['head'].fontRevision = METADATA_VERSION

            # read image size data
            self.read_cbdt(ttf)

            # glyph name to codepoint map
            glyph_to_codepoint_map = {}

            # read single codepoint emojis under cmap12 and clear the table contents
            cmap12_table = self.read_and_clear_cmap12(ttf, glyph_to_codepoint_map)

            # read emoji sequences gsub and clear the table contents
            self.read_and_clear_gsub(ttf, glyph_to_codepoint_map)

            # add all new codepoint to glyph mappings
            cmap12_table.cmap.update(self.remapped_codepoints)

            output_json_file = os.path.join(tmp_dir, OUTPUT_JSON_FILE_NAME)
            flatbuffer_bin_file = os.path.join(tmp_dir, FLATBUFFER_BIN)
            flatbuffer_java_dir = os.path.join(tmp_dir, FLATBUFFER_JAVA_PATH)

            total_emoji_count = self.write_metadata_json(output_json_file)
            self.write_metadata_csv()

            # create the flatbuffers binary and java classes
            sys_command = 'flatc -o {0} -b -j {1} {2}'
            os.system(sys_command.format(tmp_dir, FLATBUFFER_SCHEMA, output_json_file))

            # inject metadata binary into font
            inject_meta_into_font(ttf, flatbuffer_bin_file)

            # save the new font
            ttf.save(TEST_FONT_FILE_PATH)

            # copy to bundled-typeface project
            makedirs_if_not_exists(BUNDLED_ASSET_DIR)
            shutil.copy(TEST_FONT_FILE_PATH, BUNDLED_ASSET_PATH)

            update_flatbuffer_java_files(flatbuffer_java_dir)

            # clear the tmp output directory
            shutil.rmtree(tmp_dir, ignore_errors=True)

            print(
                "{0} emojis are written to\n{1}\n{2}".format(total_emoji_count, TEST_FONT_FILE_PATH,
                                                             BUNDLED_ASSET_DIR))

            print("Running support-emoji tests")
            gradle_exec = os.path.join(SUPPORT_ROOT_DIR, 'gradlew')
            test_process = subprocess.Popen([gradle_exec, 'support-emoji:connectedCheck'],
                                            cwd=SUPPORT_ROOT_DIR)
            test_process.wait()


def print_usage():
    """Prints how to use the script."""
    print("Please specify a path to font and unicode files.\n"
          "usage: createfont.py noto-color-emoji-path unicode-dir-path")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print_usage()
        sys.exit(1)
    EmojiFontCreator(sys.argv[1], sys.argv[2]).create_font()