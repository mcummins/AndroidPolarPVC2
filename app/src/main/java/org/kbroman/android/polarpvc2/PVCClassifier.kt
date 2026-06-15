package org.kbroman.android.polarpvc2

import org.kbroman.android.polarpvc2.ecg.EcgFeatures.BeatFeatures
import kotlin.math.abs

/**
 * PVC classification from per-beat features — a port of the offline
 * `analysis/polarpvc/classify.py`, so the live preview uses the same decision
 * rule and thresholds as the validated offline pipeline.
 *
 * A PVC is an aberrant complex (poor correlation to the patient's own normal
 * beat) with abnormal timing (premature OR a compensatory pause), or morphology
 * so grossly aberrant it stands alone (interpolated / non-premature PVC).
 * Aberrancy is judged from the template correlation, not QRS width: at the
 * H10's 130 Hz, width is too coarse to gate on (it missed ~85% of real PVCs);
 * width is reported only for auditing.
 *
 * Bad-signal beats are marked artifacts and excluded (not classified, dropped
 * from the burden numerator and denominator) rather than risk a false PVC.
 */
object PVCClassifier {

    data class ClassifierConfig(
        val aberrantCorr: Double = 0.85,        // template corr below this = aberrant
        val prematureRatio: Double = 0.85,      // rrBefore < ratio * baseline -> premature
        val compensatoryMin: Double = 1.8,
        val compensatoryMax: Double = 2.2,
        val strongCorr: Double = 0.50,          // morphology aberrant enough to stand alone
        val strongMaxPrematurity: Double = 1.3, // ... but only with plausible coupling
        val qrsWidthMs: Double = 110.0,         // informational only (see class doc)
        // signal-quality vetoes -> artifact (excluded)
        val minQrsWidthMs: Double = 60.0,
        val maxQrsWidthMs: Double = 190.0,
        val maxNoiseRatio: Double = 3.5,
        val maxAmplitudeMv: Double = 5.0,
    )

    data class BeatLabel(
        val index: Int,
        val t: Double,
        val isPvc: Boolean,
        val isArtifact: Boolean,
        val reasons: String,
    )

    fun classifyBeat(f: BeatFeatures, cfg: ClassifierConfig = ClassifierConfig()): BeatLabel {
        val wide = f.qrsWidthMs >= cfg.qrsWidthMs
        val aberrant = f.templateCorr < cfg.aberrantCorr
        val premature = f.prematurity.isFinite() && f.prematurity < cfg.prematureRatio
        val compensatory = f.compensatory.isFinite() && f.prematurity.isFinite() &&
            f.prematurity < cfg.prematureRatio &&
            f.compensatory >= cfg.compensatoryMin && f.compensatory <= cfg.compensatoryMax

        val abnormalTiming = premature || compensatory

        // grossly aberrant morphology stands on its own, but only with plausible
        // coupling (a very aberrant complex after a gap is noise, not a PVC)
        val strong = f.templateCorr < cfg.strongCorr &&
            (!f.prematurity.isFinite() || f.prematurity < cfg.strongMaxPrematurity)

        // signal-quality veto: gross deflection, implausible width, or a locally
        // noisy stretch is not a beat we can classify
        val artifact = abs(f.amplitudeMv) > cfg.maxAmplitudeMv ||
            f.qrsWidthMs < cfg.minQrsWidthMs ||
            f.qrsWidthMs > cfg.maxQrsWidthMs ||
            f.noiseRatio > cfg.maxNoiseRatio

        val isPvc = ((aberrant && abnormalTiming) || strong) && !artifact

        val reasons = buildList {
            if (artifact) add("artifact")
            if (aberrant) add("aberrant")
            if (wide) add("wide")
            if (premature) add("premature")
            if (compensatory) add("compensatory")
            if (strong) add("strong")
        }.joinToString("|")

        return BeatLabel(
            index = f.index,
            t = f.t,
            isPvc = isPvc,
            isArtifact = artifact,
            reasons = reasons,
        )
    }

    fun classifyBeats(feats: List<BeatFeatures>, cfg: ClassifierConfig = ClassifierConfig()): List<BeatLabel> =
        feats.map { classifyBeat(it, cfg) }
}
