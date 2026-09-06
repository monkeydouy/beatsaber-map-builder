export type DifficultyValue = "Easy" | "Normal" | "Hard" | "Expert" | "ExpertPlus";
export type StyleValue = "balanced" | "dance" | "technical";

export type JobStage = "analysis" | "generation";

export type JobStatus =
  | "CREATED"
  | "ACQUIRING_AUDIO"
  | "NORMALIZING_AUDIO"
  | "ANALYZING_AUDIO"
  | "DETECTING_BEATS"
  | "ANALYZING_SECTIONS"
  | "BUILDING_EVENT_TIMELINE"
  | "ANALYZED"
  | "SELECTING_RHYTHM"
  | "GENERATING_PATTERNS"
  | "VALIDATING_PARITY"
  | "GENERATING_OBSTACLES"
  | "GENERATING_LIGHTING"
  | "VALIDATING_MAP"
  | "EXPORTING"
  | "PACKAGING"
  | "COMPLETED"
  | "FAILED";

export interface SongMetadata {
  title?: string;
  artist?: string;
  duration?: number;
  thumbnail?: string | null;
  original_url?: string | null;
  source_type?: string;
}

export interface AnalysisSection {
  start: number;
  end: number;
  type: string;
  intensity: number;
}

export interface TempoSegment {
  start: number;
  start_beat: number;
  bpm: number;
}

export interface AnalysisSummary {
  bpm: number;
  duration: number;
  tempo_confidence: number;
  variable_tempo: boolean;
  representative_bpm: number;
  tempo_segments: TempoSegment[];
  tempo_candidates?: Array<{ bpm: number; score: number }>;
  beats_per_bar: number;
  meter: string;
  triple_subdivision: boolean;
  beat_count: number;
  onset_count: number;
  event_count: number;
  sections: AnalysisSection[];
  energy_curve: number[];
  onset_curve: number[];
}

export interface MapStatistics {
  total_notes: number;
  left_notes: number;
  right_notes: number;
  total_walls: number;
  total_bombs: number;
  total_lights: number;
  average_nps: number;
  song_nps: number;
  peak_nps: number;
  max_local_nps: number;
  mapped_duration: number;
  doubles: number;
  crossovers: number;
  resets: number;
  estimated_difficulty: string;
}

export interface ValidationReport {
  ok: boolean;
  errors: string[];
  warnings: string[];
  checks: Record<string, boolean>;
}

export interface GenerationResult {
  difficulty: DifficultyValue;
  difficulty_label: string;
  style: StyleValue;
  intensity: number;
  seed: number;
  bpm: number;
  variable_tempo: boolean;
  tempo_segment_count: number;
  beats_per_bar: number;
  triple_subdivision: boolean;
  duration: number;
  note_jump_speed: number;
  note_jump_offset: number;
  statistics: MapStatistics;
  validation: ValidationReport;
  profile: {
    target_nps_min: number;
    target_nps_max: number;
    peak_nps: number;
    effective_target_nps: number;
  };
  package: { filename: string; contents: string[]; size_bytes: number };
  song: { title: string; artist: string; mapper: string };
}

export interface Job {
  id: string;
  status: JobStatus;
  progress: number;
  current_step: string;
  stage: JobStage;
  source_type: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  metadata: SongMetadata;
  analysis: AnalysisSummary | null;
  result: GenerationResult | null;
}

export interface BatchItem {
  filename: string;
  job_id: string | null;
  status: string | null;
  error: string | null;
}

export interface BatchUploadResponse {
  accepted: number;
  rejected: number;
  items: BatchItem[];
}

export interface YouTubeBatchItem {
  url: string;
  job_id: string | null;
  title: string | null;
  channel: string | null;
  duration: number | null;
  thumbnail: string | null;
  error: string | null;
}

export interface YouTubeBatchResponse {
  accepted: number;
  rejected: number;
  items: YouTubeBatchItem[];
}

export interface DifficultyInfo {
  value: DifficultyValue;
  label: string;
  description: string;
  target_nps_min: number;
  target_nps_max: number;
  peak_nps: number;
}

export interface StyleInfo {
  value: StyleValue;
  label: string;
  description: string;
}

export interface YouTubePreview {
  video_id: string;
  title: string;
  artist: string;
  channel: string;
  duration: number;
  thumbnail: string | null;
  url: string;
}

export interface HealthInfo {
  status: string;
  version: string;
  ffmpeg: boolean;
  ytdlp: boolean;
  youtube_enabled: boolean;
  active_jobs: number;
  max_concurrent_jobs: number;
}

export interface GenerationRequest {
  title?: string;
  artist?: string;
  mapper?: string;
  difficulty: DifficultyValue;
  style: StyleValue;
  intensity: number;
  seed?: number | null;
  bpm?: number | null;
  enable_bombs?: boolean;
  enable_walls?: boolean;
  enable_lighting?: boolean;
}
