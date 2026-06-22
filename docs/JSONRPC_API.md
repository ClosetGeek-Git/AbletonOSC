# AbletonOSC JSON-RPC API reference

> **Generated** from `abletonosc/lom_schema.py` by `tools/export_schema.py` -- do not edit by hand. The descriptor is the single source of truth that also drives the dispatcher and the test battery.

Flags: **r** readable (`get`), **w** writable (`set`), **sub** observable (`subscribe`). `<i>`/`<j>` are zero-based indices in the path.

**9 objects, 144 properties, 50 methods.**

## `song`  (arity 0)

_Special listeners: `beat`._

### Properties

| path | flags | kind |
| --- | --- | --- |
| `song.arrangement_overdub` | r/w/sub | getattr/setattr |
| `song.back_to_arranger` | r/w/sub | getattr/setattr |
| `song.can_redo` | r | getattr/setattr |
| `song.can_undo` | r | getattr/setattr |
| `song.clip_trigger_quantization` | r/w/sub | getattr/setattr |
| `song.cue_points` | r | aggregate list |
| `song.current_song_time` | r/w/sub | getattr/setattr |
| `song.groove_amount` | r/w/sub | getattr/setattr |
| `song.is_ableton_link_enabled` | r/w/sub | getattr/setattr |
| `song.is_playing` | r/sub | getattr/setattr |
| `song.loop` | r/w/sub | getattr/setattr |
| `song.loop_length` | r/w/sub | getattr/setattr |
| `song.loop_start` | r/w/sub | getattr/setattr |
| `song.metronome` | r/w/sub | getattr/setattr |
| `song.midi_recording_quantization` | r/w/sub | getattr/setattr |
| `song.nudge_down` | r/w/sub | getattr/setattr |
| `song.nudge_up` | r/w/sub | getattr/setattr |
| `song.num_return_tracks` | r | computed scalar |
| `song.num_scenes` | r | computed scalar |
| `song.num_tracks` | r | computed scalar |
| `song.punch_in` | r/w/sub | getattr/setattr |
| `song.punch_out` | r/w/sub | getattr/setattr |
| `song.record_mode` | r/w/sub | getattr/setattr |
| `song.root_note` | r/w/sub | getattr/setattr |
| `song.scale_intervals` | r | getattr/setattr |
| `song.scale_mode` | r/w/sub | getattr/setattr |
| `song.scale_name` | r/w/sub | getattr/setattr |
| `song.session_record` | r/w/sub | getattr/setattr |
| `song.session_record_status` | r/sub | getattr/setattr |
| `song.signature_denominator` | r/w/sub | getattr/setattr |
| `song.signature_numerator` | r/w/sub | getattr/setattr |
| `song.song_length` | r/sub | getattr/setattr |
| `song.tempo` | r/w/sub | getattr/setattr |
| `song.tempo_follower_enabled` | r/w/sub | getattr/setattr |
| `song.track_names` | r | aggregate list |

### Methods

| path | args |
| --- | --- |
| `song.capture_and_insert_scene` | (none) |
| `song.capture_midi` | (none) |
| `song.continue_playing` | (none) |
| `song.create_audio_track` | index |
| `song.create_midi_track` | index |
| `song.create_return_track` | (none) |
| `song.create_scene` | index |
| `song.delete_return_track` | index |
| `song.delete_scene` | index |
| `song.delete_track` | index |
| `song.duplicate_scene` | index |
| `song.duplicate_track` | index |
| `song.force_link_beat_time` | (none) |
| `song.jump_by` | beats |
| `song.jump_to_next_cue` | (none) |
| `song.jump_to_prev_cue` | (none) |
| `song.name_cue` | time, name |
| `song.re_enable_automation` | (none) |
| `song.redo` | (none) |
| `song.set_or_delete_cue` | (none) |
| `song.start_playing` | (none) |
| `song.stop_all_clips` | (none) |
| `song.stop_playing` | (none) |
| `song.tap_tempo` | (none) |
| `song.trigger_session_record` | (none) |
| `song.undo` | (none) |

## `track`  (arity 1)

### Properties

| path | flags | kind |
| --- | --- | --- |
| `track.0.arm` | r/w/sub | getattr/setattr |
| `track.0.arrangement_clips.length` | r | aggregate list |
| `track.0.arrangement_clips.name` | r | aggregate list |
| `track.0.arrangement_clips.start_time` | r | aggregate list |
| `track.0.can_be_armed` | r | getattr/setattr |
| `track.0.clips.color` | r | aggregate list |
| `track.0.clips.length` | r | aggregate list |
| `track.0.clips.name` | r | aggregate list |
| `track.0.color` | r/w/sub | getattr/setattr |
| `track.0.color_index` | r/w/sub | getattr/setattr |
| `track.0.current_monitoring_state` | r/w/sub | getattr/setattr |
| `track.0.devices.class_name` | r | aggregate list |
| `track.0.devices.name` | r | aggregate list |
| `track.0.devices.type` | r | aggregate list |
| `track.0.fired_slot_index` | r/sub | getattr/setattr |
| `track.0.fold_state` | r/w | getattr/setattr |
| `track.0.has_audio_input` | r/sub | getattr/setattr |
| `track.0.has_audio_output` | r/sub | getattr/setattr |
| `track.0.has_midi_input` | r/sub | getattr/setattr |
| `track.0.has_midi_output` | r/sub | getattr/setattr |
| `track.0.is_foldable` | r | getattr/setattr |
| `track.0.is_grouped` | r | getattr/setattr |
| `track.0.is_visible` | r | getattr/setattr |
| `track.0.mute` | r/w/sub | getattr/setattr |
| `track.0.name` | r/w/sub | getattr/setattr |
| `track.0.num_devices` | r | computed scalar |
| `track.0.output_meter_left` | r/sub | getattr/setattr |
| `track.0.output_meter_level` | r/sub | getattr/setattr |
| `track.0.output_meter_right` | r/sub | getattr/setattr |
| `track.0.panning` | r/w/sub | mixer_device.<leaf>.value |
| `track.0.playing_slot_index` | r/sub | getattr/setattr |
| `track.0.send` | r/w/sub | leaf `send.<i>` -> mixer_device.sends[i].value |
| `track.0.solo` | r/w/sub | getattr/setattr |
| `track.0.volume` | r/w/sub | mixer_device.<leaf>.value |

### Methods

| path | args |
| --- | --- |
| `track.0.delete_clip` | clip_index |
| `track.0.delete_device` | device_index |
| `track.0.insert_device` | name |
| `track.0.load_device` | name |
| `track.0.stop_all_clips` | (none) |

## `scene`  (arity 1)

### Properties

| path | flags | kind |
| --- | --- | --- |
| `scene.0.color` | r/w/sub | getattr/setattr |
| `scene.0.color_index` | r/w/sub | getattr/setattr |
| `scene.0.is_empty` | r | getattr/setattr |
| `scene.0.is_triggered` | r/sub | getattr/setattr |
| `scene.0.name` | r/w/sub | getattr/setattr |
| `scene.0.tempo` | r/w/sub | getattr/setattr |
| `scene.0.tempo_enabled` | r/w/sub | getattr/setattr |
| `scene.0.time_signature_denominator` | r/w/sub | getattr/setattr |
| `scene.0.time_signature_enabled` | r/w/sub | getattr/setattr |
| `scene.0.time_signature_numerator` | r/w/sub | getattr/setattr |

### Methods

| path | args |
| --- | --- |
| `scene.0.fire` | (none) |
| `scene.0.fire_as_selected` | (none) |
| `scene.0.fire_selected` | (none) |

## `clip_slot`  (arity 2)

### Properties

| path | flags | kind |
| --- | --- | --- |
| `clip_slot.0.1.controls_other_clips` | r/sub | getattr/setattr |
| `clip_slot.0.1.has_clip` | r/sub | getattr/setattr |
| `clip_slot.0.1.has_stop_button` | r/w/sub | getattr/setattr |
| `clip_slot.0.1.is_group_slot` | r | getattr/setattr |
| `clip_slot.0.1.is_playing` | r | getattr/setattr |
| `clip_slot.0.1.is_triggered` | r/sub | getattr/setattr |
| `clip_slot.0.1.playing_status` | r/sub | getattr/setattr |
| `clip_slot.0.1.will_record_on_start` | r | getattr/setattr |

### Methods

| path | args |
| --- | --- |
| `clip_slot.0.1.create_audio_clip` | path |
| `clip_slot.0.1.create_clip` | length |
| `clip_slot.0.1.delete_clip` | (none) |
| `clip_slot.0.1.duplicate_clip_to` | target_track, target_slot |
| `clip_slot.0.1.fire` | (none) |
| `clip_slot.0.1.stop` | (none) |

## `clip`  (arity 2)

_MIDI notes: extended-dict ops `add_notes` / `get_notes` / `remove_notes` / `remove_notes_by_id` (see API conventions)._

### Properties

| path | flags | kind |
| --- | --- | --- |
| `clip.0.1.color` | r/w/sub | getattr/setattr |
| `clip.0.1.color_index` | r/w/sub | getattr/setattr |
| `clip.0.1.end_marker` | r/w/sub | getattr/setattr |
| `clip.0.1.end_time` | r/sub | getattr/setattr |
| `clip.0.1.file_path` | r/sub | getattr/setattr |
| `clip.0.1.gain` | r/w | getattr/setattr |
| `clip.0.1.gain_display_string` | r | getattr/setattr |
| `clip.0.1.has_groove` | r | getattr/setattr |
| `clip.0.1.is_audio_clip` | r | getattr/setattr |
| `clip.0.1.is_midi_clip` | r | getattr/setattr |
| `clip.0.1.is_overdubbing` | r/sub | getattr/setattr |
| `clip.0.1.is_playing` | r | getattr/setattr |
| `clip.0.1.is_recording` | r/sub | getattr/setattr |
| `clip.0.1.is_triggered` | r | getattr/setattr |
| `clip.0.1.launch_mode` | r/w/sub | getattr/setattr |
| `clip.0.1.launch_quantization` | r/w/sub | getattr/setattr |
| `clip.0.1.legato` | r/w/sub | getattr/setattr |
| `clip.0.1.length` | r | getattr/setattr |
| `clip.0.1.loop_end` | r/w/sub | getattr/setattr |
| `clip.0.1.loop_start` | r/w/sub | getattr/setattr |
| `clip.0.1.looping` | r/w/sub | getattr/setattr |
| `clip.0.1.muted` | r/w/sub | getattr/setattr |
| `clip.0.1.name` | r/w/sub | getattr/setattr |
| `clip.0.1.pitch_coarse` | r/w/sub | getattr/setattr |
| `clip.0.1.pitch_fine` | r/w/sub | getattr/setattr |
| `clip.0.1.playing_position` | r/sub | getattr/setattr |
| `clip.0.1.position` | r/w/sub | getattr/setattr |
| `clip.0.1.ram_mode` | r/w/sub | getattr/setattr |
| `clip.0.1.sample_length` | r | getattr/setattr |
| `clip.0.1.start_marker` | r/w/sub | getattr/setattr |
| `clip.0.1.start_time` | r/sub | getattr/setattr |
| `clip.0.1.velocity_amount` | r/w/sub | getattr/setattr |
| `clip.0.1.warp_mode` | r/w/sub | getattr/setattr |
| `clip.0.1.warping` | r/w/sub | getattr/setattr |
| `clip.0.1.will_record_on_start` | r | getattr/setattr |

### Methods

| path | args |
| --- | --- |
| `clip.0.1.duplicate_loop` | (none) |
| `clip.0.1.fire` | (none) |
| `clip.0.1.stop` | (none) |

## `device`  (arity 2)

### Properties

| path | flags | kind |
| --- | --- | --- |
| `device.0.1.class_name` | r | getattr/setattr |
| `device.0.1.is_using_compare_preset_b` | r/sub | getattr/setattr |
| `device.0.1.name` | r/sub | getattr/setattr |
| `device.0.1.num_parameters` | r | computed scalar |
| `device.0.1.parameter.is_quantized` | r | leaf `parameter.<i>.<sub>` -> parameters[i].<sub> |
| `device.0.1.parameter.max` | r | leaf `parameter.<i>.<sub>` -> parameters[i].<sub> |
| `device.0.1.parameter.min` | r | leaf `parameter.<i>.<sub>` -> parameters[i].<sub> |
| `device.0.1.parameter.name` | r | leaf `parameter.<i>.<sub>` -> parameters[i].<sub> |
| `device.0.1.parameter.value` | r/w/sub | leaf `parameter.<i>.<sub>` -> parameters[i].<sub> |
| `device.0.1.parameter.value_string` | r | leaf `parameter.<i>.<sub>` -> parameters[i].<sub> |
| `device.0.1.parameters.is_quantized` | r | aggregate list |
| `device.0.1.parameters.max` | r | aggregate list |
| `device.0.1.parameters.min` | r | aggregate list |
| `device.0.1.parameters.name` | r | aggregate list |
| `device.0.1.parameters.value` | r | aggregate list |
| `device.0.1.type` | r | getattr/setattr |

### Methods

| path | args |
| --- | --- |
| `device.0.1.set_parameters` | values... |

## `view`  (arity 0)

### Properties

| path | flags | kind |
| --- | --- | --- |
| `view.selected_clip` | r/w | computed scalar |
| `view.selected_device` | r/w | computed scalar |
| `view.selected_scene` | r/w/sub | computed scalar |
| `view.selected_track` | r/w/sub | computed scalar |

## `application`  (arity 0)

### Properties

| path | flags | kind |
| --- | --- | --- |
| `application.average_process_usage` | r | computed scalar |
| `application.version` | r | computed scalar |

### Methods

| path | args |
| --- | --- |
| `application.get_log_level` | (none) |
| `application.ping` | (none) |
| `application.reload` | (none) |
| `application.set_log_level` | level |
| `application.show_message` | message |

## `midimap`  (arity 0)

### Methods

| path | args |
| --- | --- |
| `midimap.map_cc` | track, device, parameter, channel, cc |

