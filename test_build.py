"""Offline checks for build.py: parsers, preprocessor, merge."""
import unittest

import tempfile
from pathlib import Path

from build import (ABOUT, BLINK_ENTRY, about_flags, SWITCH, SWITCH_MACRO, doc, feature_default, features, is_value, merge,
                   preprocess)

SWITCHES = '''// Select which ANGLE backend to use.
const char kUseANGLE[] = "use-angle";
const char kANGLEImplementationVulkanName[] = "vulkan";
const char kProcessType[] = "type";
const char kProcessTypeController[] = "controller";
const char kDisableGpu[] = "disable-gpu";
// Disables GPU compositing.
const char kDisableGpuCompositing[] =
    "disable-gpu-compositing";
NETWORK_SWITCH(kIgnoreCertificateErrors, "ignore-certificate-errors")
'''

VULKAN = '''
#if BUILDFLAG(IS_ANDROID)
             base::FEATURE_ENABLED_BY_DEFAULT
#elif BUILDFLAG(IS_WIN) || BUILDFLAG(IS_MAC)
             base::FEATURE_ENABLED_BY_DEFAULT
#else
             base::FEATURE_DISABLED_BY_DEFAULT
#endif
'''

BLINK = '''    {
      // WebGPU developer features.
      name: "WebGPUDeveloperFeatures",
      status: {"Android": "stable", "default": "experimental"},
    },
    {
      name: "Plain",
    },
'''


class Parse(unittest.TestCase):
    def test_switches_and_values(self):
        defs = SWITCH.findall(SWITCHES) + SWITCH_MACRO.findall(SWITCHES)
        real = {name: doc(comment) for comment, const, name in defs if not is_value(const, name, defs)}
        self.assertEqual(real, {"use-angle": "Select which ANGLE backend to use.", "type": "",
                                "disable-gpu": "", "disable-gpu-compositing": "Disables GPU compositing.",
                                "ignore-certificate-errors": ""})

    def test_feature_default_per_os(self):
        (comment, const, args), = features("// Enable Vulkan.\nBASE_FEATURE(kVulkan," + VULKAN + ");\n")
        self.assertEqual((doc(comment), const), ("Enable Vulkan.", "kVulkan"))
        self.assertEqual({o: feature_default(args, o) for o in ("linux", "mac", "win")},
                         {"linux": "off", "mac": "on", "win": "on"})
        (_, _, args), = features('BASE_FEATURE(kX, "X", base::FEATURE_ENABLED_BY_DEFAULT);\n')
        self.assertEqual(feature_default(args, "linux"), "on")
        (_, const, args), = features("BASE_FEATURE(AcceleratedVideoDecodeLinuxGL, base::FEATURE_DISABLED_BY_DEFAULT);\n")
        self.assertEqual((const, feature_default(args, "linux")), ("AcceleratedVideoDecodeLinuxGL", "off"))
        split = "BASE_FEATURE(kEGL,\n#if BUILDFLAG(IS_MAC)\n base::FEATURE_ENABLED_BY_DEFAULT);\n#else\n base::FEATURE_DISABLED_BY_DEFAULT);\n#endif\n"
        (_, _, args), = features(split)
        self.assertEqual([feature_default(args, o) for o in ("linux", "mac")], ["off", "on"])
        for token, want in [("base::DISABLED", ["off", "off"]), ("enable_if(!IS_ANDROID)", ["on", "on"]),
                            ("enable_if(IS_MAC)", ["off", "on"]), ("enabled_by_default_desktop_only", ["on", "on"]),
                            ("enabled_by_default_android_only", ["off", "off"]), ("kDefaultEnabled", ["kDefaultEnabled"] * 2)]:
            (_, _, args), = features(f"BASE_FEATURE(kX, {token});")
            self.assertEqual([feature_default(args, o) for o in ("linux", "mac")], want, token)

    def test_preprocess_nested(self):
        text = "a\n#if !BUILDFLAG(IS_ANDROID)\nb\n#if BUILDFLAG(IS_WIN)\nc\n#endif\n#endif\n#if BUILDFLAG(ENABLE_EXTENSIONS)\nd\n#endif"
        self.assertEqual(preprocess(text, "linux"), "a\nb\nd")
        self.assertEqual(preprocess(text, "win"), "a\nb\nc\nd")
        cont = "#if BUILDFLAG(IS_WIN) || \\\n    BUILDFLAG(IS_CHROMEOS)\non\n#else\noff\n#endif"
        self.assertEqual([preprocess(cont, o) for o in ("linux", "win")], ["off", "on"])

    def test_blink(self):
        found = [(doc(c), n) for c, n, _ in BLINK_ENTRY.findall(BLINK)]
        self.assertEqual(found, [("WebGPU developer features.", "WebGPUDeveloperFeatures"), ("", "Plain")])

    def test_class_qualified_feature(self):
        (_, const, args), = features('BASE_FEATURE(Zps::kZpsLimit, "OmniboxZpsLimit", base::FEATURE_DISABLED_BY_DEFAULT);')
        self.assertEqual(const, "kZpsLimit")

    def test_about_flags(self):
        about = '''const FeatureEntry::Choice kPairs[] = {{"a", "b"}};
const FeatureEntry::Choice kDirChoices[] = {
    {flags_ui::kGenericExperimentChoiceDefault, "", ""},
    {flag_descriptions::kRtl, switches::kForceUIDirection,
     switches::kForceDirectionRTL}};
constexpr char kDemuxList[] =
    "DemuxA,"
    "DemuxB";
const FeatureEntry::Choice kDemuxChoices[] = {
    {"On", switches::kEnableFeatures, kDemuxList}};
const FeatureEntry kFeatureEntries[] = {
    {"force-ui-direction", flag_descriptions::kN, flag_descriptions::kD, kOsAll,
     MULTI_VALUE_TYPE(kDirChoices)},
    {"demux", flag_descriptions::kN, flag_descriptions::kD, kOsDesktop,
     MULTI_VALUE_TYPE(kDemuxChoices)},
    {"actor", flag_descriptions::kN, flag_descriptions::kD, kOsMac,
     ENABLE_DISABLE_VALUE_TYPE_AND_VALUE(switches::kEnableFeatures, "Actor,"
                                         "ActorUi:p/1", switches::kDisableFeatures, "Actor")},
};
'''
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ABOUT).parent.mkdir(parents=True)
            (Path(tmp) / ABOUT).write_text(about)
            out = about_flags(Path(tmp), {"kForceUIDirection": "force-ui-direction"},
                              {"DemuxA", "DemuxB", "Actor", "ActorUi"})
        self.assertEqual({k: v["id"] for k, v in out.items()},
                         {"force-ui-direction": "force-ui-direction", "DemuxA": "demux", "DemuxB": "demux",
                          "Actor": "actor", "ActorUi": "actor"})
        self.assertEqual(out["Actor"]["os"], ["mac"])


class Merge(unittest.TestCase):
    def test_ranges_per_os(self):
        def rec(m, os, default="off", infobar=()):
            return {"milestone": m, "flags": {"X": {"kind": ["feature"], "doc": f"m{m}", "file": "f.cc", "os": os,
                                                    "default": {o: default for o in os}, "infobar": list(infobar)}}}
        records = [rec(113, ["linux", "win"]), rec(114, ["linux"], infobar=["linux"]), rec(115, ["linux"], "on"),
                   rec(117, ["linux", "win"], "on")]
        out = merge(records, [113, 114, 115, 117])["X"]
        self.assertEqual(out["doc"], "m117")
        self.assertEqual(out["os"], {"linux": [[113, 117]], "win": [[113, 113], [117, 117]]})
        self.assertEqual(out["infobar"], {"linux": [[114, 114]]})
        self.assertEqual(out["default"]["linux"], [[113, 114, "off"], [115, 117, "on"]])


if __name__ == "__main__":
    unittest.main()
