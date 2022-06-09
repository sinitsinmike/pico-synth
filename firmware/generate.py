#!/usr/bin/env python3
'''
pico-synth: A Raspberry Pi Pico based digital synthesizer.

SPDX-FileCopyrightText: 2021-2022 Rafael G. Martins <rafael@rafaelmartins.eng.br>
SPDX-License-Identifier: BSD-3-Clause
'''

import itertools
import math
import os

cpu_frequency = 133000000
cpu_cycles_per_sample = 34

waveform_amplitude = 0x7ff
waveform_samples_per_cycle = 0x200

a4_midi_number = 69
a4_frequency = 440.0

audio_sample_rate = 48000

note_prefixes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
note_frequencies = [a4_frequency * 2 ** ((i - a4_midi_number) / 12)
                    for i in range(128)]


'''
Bandlimited wavetables

References:
    - Tim Stilson and Julius Smith. 1996. Alias-Free Digital Synthesis of Classic
      Analog Waveforms (https://ccrma.stanford.edu/~stilti/papers/blit.pdf)
'''

# the octave closer to the nyquist frequency is usually a sine, then we reuse
# the existing sine wavetable.
wavetable_octaves = math.ceil(len(note_frequencies) / 12) - 1


def fix_wavetable(array):
    mn = min(array)
    mx = max(array)

    for i in range(len(array)):
        array[i] -= mn
        array[i] *= (2 * waveform_amplitude) / abs(mx - mn)
        array[i] = int(array[i]) - waveform_amplitude

    return array[::-1]


wavetables = {
    'sine': [waveform_amplitude * math.sin(2 * math.pi * i / waveform_samples_per_cycle)
             for i in range(waveform_samples_per_cycle)],
    'sawtooth': [],
    'square': [],
    'triangle': [],
}

for i in range(wavetable_octaves):
    idx = i * 12 + 11
    f = note_frequencies[idx if idx < len(note_frequencies)
                         else len(note_frequencies) - 1]

    P = audio_sample_rate / f
    M = 2 * math.floor(P / 2) + 1

    mid = 0
    blit = []
    for i in range(waveform_samples_per_cycle):
        x = (i - waveform_samples_per_cycle / 2) / waveform_samples_per_cycle
        try:
            blit.append(math.sin(math.pi * x * M) / (M * math.sin(math.pi * x)))
        except ZeroDivisionError:
            mid = i
            blit.append(1.0)
    blit_avg = min(blit) + ((max(blit) - min(blit)) / 2)

    y = 0
    square = []
    for i in range(len(blit)):
        y += blit[i] - blit[i + mid if i < mid else i - mid]
        square.append(y)
    square_avg = min(square) + ((max(square) - min(square)) / 2)
    wavetables['square'].append(fix_wavetable(square))

    y = 0
    triangle = []
    for v in square:
        y += v - square_avg
        triangle.append(y)
    triangle_start = waveform_samples_per_cycle // 4
    triangle = triangle[triangle_start:] + triangle[:triangle_start]
    wavetables['triangle'].append(fix_wavetable(triangle))

    y = 0
    sawtooth = []
    for i in range(len(blit)):
        y += blit[i + mid if i < mid else i - mid] - 1. / P
        sawtooth.append(-y)
    wavetables['sawtooth'].append(fix_wavetable(sawtooth))


def format_hex(v, zero_padding=4):
    return '%s0x%0*x' % (int(v) < 0 and '-' or '', zero_padding, abs(int(v)))


def header():
    yield '/*'
    yield ' * pico-synth: A Raspberry Pi Pico based digital synthesizer.'
    yield ' *'
    yield ' * SPDX-FileCopyrightText: 2021-2022 Rafael G. Martins <rafael@rafaelmartins.eng.br>'
    yield ' * SPDX-License-Identifier: BSD-3-Clause'
    yield ' */'
    yield ''
    yield '// this file was generated by generate.py. do not edit!'
    yield ''
    yield '#pragma once'


def dump_headers(headers, system=True):
    if headers:
        yield ''

    for header in headers:
        yield '#include %s%s%s' % (system and '<' or '"', header, system and '>' or '"')


def dump_macros(items):
    if items:
        yield ''

    for item in items:
        yield '#define %s %s' % (item, items[item])


def dump_notes():
    yield ''
    yield 'static const ps_engine_note_t notes[] = {'

    for i, f in enumerate(note_frequencies):
        step = waveform_samples_per_cycle / (audio_sample_rate / f)
        yield '    {'
        yield '        .id        = %d,' % i
        yield '        .name      = "%s%d",' % (note_prefixes[i % 12], (i // 12) - 1)
        yield '        .step.data = %s,' % format_hex(step * (1 << 16), 8)
        yield '    },'

    yield '};'


def dump_wavetables():
    for var, array in wavetables.items():
        if len(array) == 0:
            continue

        yield ''

        if not isinstance(array[0], list):
            yield 'static const int16_t %s_wavetable[%s] = {' % \
                (var, format_hex(len(array)))
            for i in range(0, len(array) // 8):
                yield '    %s,' % ', '.join([format_hex(j)
                                             for j in array[i * 8: (i + 1) * 8]])
            yield '};'
            continue

        yield 'static const int16_t %s_wavetables[%d][%s] = {' % \
            (var, len(array), format_hex(len(array[0])))
        for value in array:
            yield '    {'
            for i in range(0, len(value) // 8):
                yield '        %s,' % ', '.join([format_hex(j)
                                                 for j in value[i * 8: (i + 1) * 8]])
            yield '    },'
        yield '};'


generators = {
    os.path.join('engine', 'driver-mcp4822-data.h'): itertools.chain(
        dump_macros({
            'mcp4822_clkdiv': cpu_frequency / (audio_sample_rate * cpu_cycles_per_sample),
        }),
    ),
    os.path.join('engine', 'engine-data.h'): itertools.chain(
        dump_macros({
            'waveform_amplitude': format_hex(waveform_amplitude),
        }),
    ),
    os.path.join('engine', 'note-data.h'): itertools.chain(
        dump_headers(['pico-synth/engine.h']),
        dump_macros({
            'notes_last': len(note_frequencies) - 1,
        }),
        dump_notes(),
    ),
    os.path.join('engine', 'module-oscillator-data.h'): itertools.chain(
        dump_headers(['stdint.h']),
        dump_macros({
            'waveform_samples_per_cycle': format_hex(waveform_samples_per_cycle),
            'wavetable_octaves': wavetable_octaves,
        }),
        dump_wavetables(),
    ),
    os.path.join('synth-data.h'): itertools.chain(
        dump_macros({
            'cpu_frequency': cpu_frequency // 1000,
        }),
    ),
}


if __name__ == '__main__':
    rootdir = os.path.dirname(os.path.abspath(__file__))
    for key in generators:
        print('generating %s ...' % key)

        with open(os.path.join(rootdir, key), 'w') as fp:
            for l in itertools.chain(header(), generators[key]):
                print(l, file=fp)
