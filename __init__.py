from pythonforandroid.recipe import PyProjectRecipe


class CharsetNormalizerRecipe(PyProjectRecipe):
    # Pin 3.3.2: the pure-Python (py3-none-any) release. 3.5.2 is the
    # version that broke CI #1/#2: p4a's pip re-resolution (build.py
    # process_python_modules) re-added a 3.5.2 platform wheel to the
    # pip --target install list, overriding the spec's ==3.3.2 pin.
    # As a recipe, charset-normalizer is no longer a pip module, so
    # that re-resolution list no longer contains it.
    #
    # How this recipe installs (verified against the local p4a source
    # in ~/workspace/hongguo_apk/.buildozer/.../pythonforandroid):
    #  - PyProjectRecipe.build_arch() first calls check_prebuilt(),
    #    which pip dry-runs charset-normalizer==3.3.2 with
    #    --only-binary and the android platform tags; the 3.3.2
    #    py3-none-any wheel satisfies those tags, so
    #    install_prebuilt_wheel() installs it directly.
    #  - No url is set on purpose: download() is skipped when url is
    #    None, so nothing is fetched for a source build. If the
    #    prebuilt lookup ever fails, build_arch() will warn and skip
    #    instead of building from source -- that failure mode, not a
    #    silent wrong version, is what to look for in the CI log.
    #  - The spec still carries charset-normalizer==3.3.2; the
    #    toolchain turns that into VERSION_charset-normalizer=3.3.2,
    #    which the recipe's version property reads, so the pin and
    #    the recipe cannot drift apart.
    version = "3.3.2"


recipe = CharsetNormalizerRecipe()
