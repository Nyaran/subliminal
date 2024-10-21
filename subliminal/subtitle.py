"""Subtitle class."""

from __future__ import annotations

import codecs
import logging
import os
from codecs import BOM_UTF8, BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

import chardet
import srt  # type: ignore[import-untyped]
from pysubs2 import SSAFile, UnknownFPSError  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from babelfish import Language  # type: ignore[import-untyped]

    from subliminal.video import Video

logger = logging.getLogger(__name__)

#: Subtitle formats to extension
FORMAT_TO_EXTENSION = {
    'srt': '.srt',
    'ass': '.ass',
    'ssa': '.ssa',
    'microdvd': '.sub',
    'mpl2': '.mpl',
    'tmp': '.txt',
    'vtt': '.vtt',
}

#: Subtitle extensions
SUBTITLE_EXTENSIONS = (*FORMAT_TO_EXTENSION.values(), '.smi')

#: BOMs for UTF contents, first UTF32 BOMS as BOM_UTF32_LE startswith BOM_UTF16_LE
BOMS = (
    (BOM_UTF8, 'utf-8-sig'),
    (BOM_UTF32_BE, 'utf-32-be'),
    (BOM_UTF32_LE, 'utf-32-le'),
    (BOM_UTF16_BE, 'utf-16-be'),
    (BOM_UTF16_LE, 'utf-16-le'),
)


#: Subtitle language types
class LanguageType(Enum):
    """Subtitle language types."""

    UNKNOWN = 'unknown'
    FORCED = 'forced'
    NORMAL = 'normal'
    HEARING_IMPAIRED = 'hearing_impaired'

    @classmethod
    def from_flags(cls, *, hearing_impaired: bool | None = None, forced: bool | None = None) -> LanguageType:
        """Convert to LanguageType from flags."""
        language_type = cls.UNKNOWN
        if hearing_impaired:
            language_type = cls.HEARING_IMPAIRED
        elif forced:
            language_type = cls.FORCED
        # if hearing_impaired or forced is specified to be False
        # then for sure the subtitle is normal.
        elif hearing_impaired is False or forced is False:
            language_type = cls.NORMAL

        return language_type

    def is_hearing_impaired(self) -> bool | None:
        """Flag for hearing impaired."""
        if self == LanguageType.HEARING_IMPAIRED:
            return True
        if self == LanguageType.UNKNOWN:
            return None
        return False

    def is_forced(self) -> bool | None:
        """Flag for forced."""
        if self == LanguageType.FORCED:
            return True
        if self == LanguageType.UNKNOWN:
            return None
        return False


class Subtitle:
    """Base class for subtitle.

    :param language: language of the subtitle.
    :type language: :class:`~babelfish.language.Language`
    :param (bool | None) hearing_impaired: whether or not the subtitle is hearing impaired (None if unknown).
    :param page_link: URL of the web page from which the subtitle can be downloaded.
    :type page_link: str
    :param encoding: Text encoding of the subtitle.
    :type encoding: str

    """

    #: Name of the provider that returns that class of subtitle
    provider_name: ClassVar[str] = ''

    #: Content as bytes
    _content: bytes | None

    #: Content as string
    _text: str

    #: Flag to assert if the subtitle raw content was decoded
    _is_decoded: bool

    #: Flag to assert if the subtitle is valid (None if it was not checked yet)
    _is_valid: bool | None

    #: Guess encoding if None is defined
    _guess_encoding: bool

    #: Language of the subtitle
    language: Language

    #: Subtitle id
    subtitle_id: str

    #: Whether or not the subtitle is hearing impaired (None if unknown)
    language_type: LanguageType

    #: URL of the web page from which the subtitle can be downloaded
    page_link: str | None

    #: Encoding to decode with when accessing :attr:`text`
    encoding: str | None

    #: Subtitle format, None for automatic detection
    subtitle_format: str | None = None

    #: Framerate for frame-based formats (MicroDVD)
    fps: float | None

    #: Whether the subtitle is embedded in the video or an external file
    embedded: bool

    def __init__(
        self,
        language: Language,
        subtitle_id: str = '',
        *,
        hearing_impaired: bool | None = None,
        forced: bool | None = None,
        page_link: str | None = None,
        encoding: str | None = None,
        subtitle_format: str | None = None,
        fps: float | None = None,
        embedded: bool = False,
        guess_encoding: bool = True,
    ) -> None:
        self._content = None
        self._text = ''
        self._is_decoded = False
        self._is_valid = None
        self._guess_encoding = guess_encoding

        self.language = language
        self.subtitle_id = subtitle_id
        self.page_link = page_link
        self.subtitle_format = subtitle_format
        self.fps = fps
        self.embedded = embedded

        self.language_type = LanguageType.from_flags(hearing_impaired=hearing_impaired, forced=forced)
        self.encoding = None
        # validate the encoding
        if encoding:
            try:
                self.encoding = codecs.lookup(encoding).name
            except (TypeError, LookupError):
                logger.debug('Unsupported encoding %s', encoding)

    @property
    def id(self) -> str:
        """Unique identifier of the subtitle."""
        return str(self.subtitle_id)

    @property
    def info(self) -> str:
        """Info of the subtitle, human readable. Usually the subtitle name for GUI rendering."""
        return self.id

    @property
    def hearing_impaired(self) -> bool | None:
        """Whether the subtitle is for hearing impaired."""
        return self.language_type.is_hearing_impaired()

    @property
    def forced(self) -> bool | None:
        """Whether the subtitle is a forced subtitle."""
        return self.language_type.is_forced()

    @property
    def content(self) -> bytes | None:
        """Content as bytes.

        If :attr:`encoding` is None, the encoding is guessed with :meth:`guess_encoding`

        """
        return self._content

    @content.setter
    def content(self, value: bytes | None) -> None:
        self.clear_content()
        self._content = value

        if self._guess_encoding and self.encoding is None:
            self.encoding = self.guess_encoding()

    @property
    def text(self) -> str:
        """Content as string."""
        if not self._is_decoded:
            self._text = self._decode_content()
        return self._text

    def clear_content(self) -> None:
        """Clear the content of the subtitle."""
        self._text = ''
        self._is_decoded = False
        self._is_valid = None

    def _decode_content(self) -> str:
        self._is_decoded = True

        if not isinstance(self.content, bytes) or not self.content:
            return ''

        # No encoding found
        if not self.encoding:
            logger.warning('Cannot guess encoding to decode subtitle content.')
            return ''

        # Decode
        return self.content.decode(self.encoding, errors='replace')

    def reencode(self, encoding: str = 'utf-8') -> bool:
        """Re-encode the subtitle raw content using the specified encoding.

        :param str encoding: the new encoding of the raw content (default to 'utf-8').
        :return: False if the encoding raised a UnicodeEncodeError error.
        :rtype: bool

        """
        # Compute self._text by calling the property
        text = self.text

        # Text is empty, maybe because the content was not decoded.
        # Reencoding would erase the content, so return.
        if not text:  # pragma: no cover
            return False

        # Try re-encoding
        try:
            new_content = text.encode(encoding=encoding)
        except UnicodeEncodeError:  # pragma: no cover
            logger.exception('Cannot encode text to bytes with encoding: %s', encoding)
            return False

        # Save the new encoding and new raw content
        self.encoding = encoding
        self._content = new_content
        return True

    def is_valid(self, *, auto_fix_srt: bool = False) -> bool:
        """Check if a :attr:`text` is a valid SubRip format.

        :return: whether or not the subtitle is valid.
        :rtype: bool

        """
        if self._is_valid is None:  # pragma: no branch
            self._is_valid = self._check_is_valid(auto_fix_srt=auto_fix_srt)

        return bool(self._is_valid)

    def _check_is_valid(self, *, auto_fix_srt: bool = False) -> bool:
        """Check if a :attr:`text` is a valid SubRip format.

        :return: whether or not the subtitle is valid.
        :rtype: bool

        """
        if not self.text:
            return False

        # Try guessing the subtitle format
        if self.subtitle_format is None:
            guessed_format = get_subtitle_format(self.text, subtitle_format=self.subtitle_format, fps=self.fps)
            # Cannot guess format
            if not guessed_format:
                return False

            # Keep the guessed format
            self.subtitle_format = guessed_format

        # Valid srt
        if self.subtitle_format == 'srt':
            try:
                parsed = self.parse_srt()
            except Exception:  # pragma: no cover
                msg = 'srt parsing failed, subtitle is invalid'
                logger.exception(msg)
                return False
            else:
                if auto_fix_srt:
                    self._text = parsed
                return True

        # TODO: check other formats
        return True

    def parse_srt(self) -> str:
        """Text content parsed to a valid srt subtitle."""
        return str(srt.compose(srt.parse(self.text)))

    def guess_encoding(self) -> str | None:
        """Guess encoding using the language, falling back on chardet.

        :return: the guessed encoding.
        :rtype: str

        """
        if not isinstance(self.content, bytes):  # pragma: no cover
            return None
        logger.info('Guessing encoding for language %s', self.language)

        # always try utf-8 first
        encodings = ['utf-8']

        # add UTF encodings matched by the BOM
        encodings.extend(find_encoding_with_bom(self.content))

        # add language-specific encodings
        encodings.extend(find_potential_encodings(self.language))

        # try to decode
        logger.debug('Trying encodings %r', encodings)
        for encoding in encodings:
            try:
                decoded = self.content.decode(encoding)
                # remove whitespace other than spaces from the string
                # see https://docs.python.org/3/library/stdtypes.html#str.isprintable
                decoded = decoded.replace('\r', '').replace('\n', '').replace('\t', '')
                if not decoded.isprintable():  # pragma: no cover
                    continue
            except UnicodeDecodeError:
                pass
            else:
                logger.info('Guessed encoding %s', encoding)
                return encoding

        logger.warning('Could not guess encoding from language')

        # fallback on chardet
        encoding_or_none = chardet.detect(self.content)['encoding']
        logger.info('Chardet found encoding %s', encoding_or_none)

        return encoding_or_none

    def get_path(
        self,
        video: Video,
        *,
        single: bool = False,
        extension: str | None = None,
        language_type_suffix: bool = False,
        language_format: str = 'alpha2',
    ) -> str:
        """Get the subtitle path using the `video`, `language` and `extension`.

        :param video: path to the video.
        :type video: :class:`~subliminal.video.Video`
        :param bool single: save a single subtitle, default is to save one subtitle per language.
        :param (str | None) extension: the subtitle extension, default is to match to the subtitle format.
        :param bool language_type_suffix: add a suffix 'hi' or 'forced' if needed. Default to False.
        :param str language_format: format of the language suffix. Default to 'alpha2'.
        :return: path of the subtitle.
        :rtype: str

        """
        if extension is None:
            extension = FORMAT_TO_EXTENSION.get(self.subtitle_format, '.srt')  # type: ignore[arg-type]

        suffix = (
            ''
            if single
            else get_subtitle_suffix(
                self.language,
                language_format=language_format,
                language_type=self.language_type,
                language_type_suffix=language_type_suffix,
            )
        )
        return get_subtitle_path(video.name, suffix=suffix, extension=extension)

    def get_matches(self, video: Video) -> set[str]:
        """Get the matches against the `video`.

        :param video: the video to get the matches with.
        :type video: :class:`~subliminal.video.Video`
        :return: matches of the subtitle.
        :rtype: set

        """
        raise NotImplementedError

    def __hash__(self) -> int:
        return hash(self.provider_name + '-' + self.id)

    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} {self.id!r} [{self.language}]>'


class EmbeddedSubtitle(Subtitle):
    """Embedded subtitle."""

    def __init__(
        self,
        language: Language,
        *,
        hearing_impaired: bool | None = None,
        forced: bool | None = None,
        encoding: str | None = None,
        subtitle_format: str | None = None,
    ) -> None:
        subtitle_id = f'Embedded <{language}>'

        super().__init__(
            language,
            subtitle_id,
            hearing_impaired=hearing_impaired,
            forced=forced,
            encoding=encoding,
            subtitle_format=subtitle_format,
            embedded=True,
        )

    @property
    def info(self) -> str:
        """Info of the subtitle, human readable. Usually the subtitle name for GUI rendering."""
        extra = ''
        if self.language_type == LanguageType.HEARING_IMPAIRED:
            extra = ' [hi]'
        elif self.language_type == LanguageType.FORCED:
            extra = ' [forced]'

        return f'{self.id}{extra}'


def get_subtitle_format(
    text: str,
    subtitle_format: str | None = None,
    fps: float | None = None,
) -> str | None:
    """Detect the subtitle format with `pysubs2`.

    :param str text: the subtitle text.
    :param (str | None) subtitle_format: the expected subtitle_format, None for auto-detect.
    :param (str | None) fps: the framerate for framerate based subtitles.
    :return: the guessed format or None if not found.
    :rtype: str | None

    """
    try:
        obj = SSAFile.from_string(text, format_=subtitle_format, fps=fps)
    except UnknownFPSError:
        default_fps = 24
        return get_subtitle_format(text, subtitle_format=subtitle_format, fps=default_fps)
    except Exception:  # pragma: no cover
        logger.exception('not a valid subtitle.')
    else:
        return str(obj.format)
    return None  # pragma: no cover


def get_subtitle_suffix(
    language: Language,
    *,
    language_format: str = 'alpha2',
    language_type: LanguageType = LanguageType.UNKNOWN,
    language_type_suffix: bool = False,
) -> str:
    """Get the subtitle suffix using the `language` and `language_type`.

    :param language: language of the subtitle to put in the path.
    :type language: :class:`~babelfish.language.Language`
    :param str language_format: format of the language suffix. Default to 'alpha2'.
    :param LanguageType language_type: the language type of the subtitle (hearing impaired or forced).
    :param bool language_type_suffix: add a suffix 'hi' or 'forced' if needed. Default to False.
    :return: suffix to the subtitle name.
    :rtype: str

    """
    only_language_formats = ('alpha2', 'alpha3', 'alpha3b', 'alpha3t', 'name')

    # Language part
    language_part = ''
    if language:
        # Defined language, not Language('und')
        try:
            language_str = getattr(language, language_format)
        except AttributeError:  # pragma: no cover
            logger.warning('cannot convert language %s using scheme: %s', language, language_format)
            language_str = str(language)

        language_part = f'.{language_str}'
        if language_format in only_language_formats:  # pragma: no branch
            # Add country and script if present
            if language.country is not None:
                # add country
                language_part += f'-{language.country!s}'
            if language.script is not None:
                # add script
                language_part += f'-{language.script!s}'

    # Language type part
    language_type_part = ''
    if language_type_suffix:
        if language_type == LanguageType.HEARING_IMPAIRED:
            language_type_part = '.hi'
        elif language_type == LanguageType.FORCED:
            language_type_part = '.forced'

    return language_type_part + language_part


def find_potential_encodings(language: Language) -> list[str]:  # pragma: no cover
    """Find potential encodings given the language."""
    # https://scratchpad.wikia.com/wiki/Character_Encoding_Recommendation_for_Languages

    if language.alpha3 == 'zho':
        return ['cp936', 'gb2312', 'gbk', 'hz', 'iso2022_jp_2', 'cp950', 'big5hkscs', 'big5', 'gb18030', 'utf-16']

    if language.alpha3 == 'jpn':
        return [
            'shift-jis',
            'cp932',
            'euc_jp',
            'iso2022_jp',
            'iso2022_jp_1',
            'iso2022_jp_2',
            'iso2022_jp_2004',
            'iso2022_jp_3',
            'iso2022_jp_ext',
        ]

    if language.alpha3 == 'tha':
        return ['tis-620', 'cp874']

    if language.alpha3 in ('ara', 'fas', 'per'):
        return ['windows-1256', 'utf-16', 'utf-16le', 'ascii', 'iso-8859-6']

    if language.alpha3 == 'heb':
        return ['windows-1255', 'iso-8859-8']

    if language.alpha3 == 'tur':
        return ['windows-1254', 'iso-8859-9', 'iso-8859-3']

    if language.alpha3 in ('grc', 'gre', 'ell'):
        return ['windows-1253', 'cp1253', 'cp737', 'iso8859-7', 'cp875', 'cp869', 'iso2022_jp_2', 'mac_greek']

    if language.alpha3 in (
        'pol',
        'cze',
        'ces',
        'slk',
        'slo',
        'slv',
        'hun',
        'bos',
        'hbs',
        'hrv',
        'rsb',
        'ron',
        'rum',
        'sqi',
        'alb',
    ):
        encodings = ['windows-1250', 'iso-8859-2']

        if language.alpha3 == 'slv':
            encodings.extend(['iso-8859-4'])
        elif language.alpha3 in ('sqi', 'alb'):
            encodings.extend(['windows-1252', 'iso-8859-15', 'iso-8859-1', 'iso-8859-9'])
        return encodings

    if language.alpha3 in ('bul', 'mkd', 'mac', 'rus', 'ukr'):
        return ['windows-1251', 'iso-8859-5']

    if language.alpha3 == 'srp':
        if language.script is not None and language.script.code == 'Latn':
            return ['windows-1250', 'iso-8859-2']
        if language.script is not None and language.script.code == 'Cyrl':
            return ['windows-1251', 'iso-8859-5']
        return ['windows-1250', 'windows-1251', 'iso-8859-2', 'iso-8859-5']

    # Western European (windows-1252) / Northern European
    return ['windows-1252', 'iso-8859-15', 'iso-8859-9', 'iso-8859-4', 'iso-8859-1']


def get_subtitle_path(
    video_path: str | os.PathLike,
    suffix: str = '',
    extension: str = '.srt',
) -> str:
    """Get the subtitle path using the `video_path` and `language`.

    :param str video_path: path to the video.
    :param str suffix: suffix with the language of the subtitle to put in the path.
    :param str extension: extension of the subtitle.
    :return: path of the subtitle.
    :rtype: str

    """
    # Full name and path
    subtitle_root = os.path.splitext(video_path)[0]

    return subtitle_root + suffix + extension


def find_encoding_with_bom(data: bytes) -> list[str]:
    """Find the UTF encoding if the raw content is starting with a byte order mask (BOM).

    Only return the first encoding that match the BOM or an empty list if no match.
    """
    return [encoding for bom, encoding in BOMS if data.startswith(bom)][:1]


def fix_line_ending(content: bytes) -> bytes:
    r"""Fix line ending of `content` by changing it to \n.

    :param bytes content: content of the subtitle.
    :return: the content with fixed line endings.
    :rtype: bytes

    """
    return content.replace(b'\r\n', b'\n')
