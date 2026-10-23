#include "mod/Internal.h"

#include "mc/client/gui/oreui/SceneProvider.h"
#include "mc/client/gui/oreui/binding/FacetRegistryFactory.h"
#include "mc/client/gui/oreui/binding/ScreenModelProvider.h"
#include "mc/client/gui/screens/models/MainMenuScreenModel.h"

using namespace probe::detail;

namespace probe::detail {

bool queryXblSignedIn(bool& signedIn) {
    auto client = ll::service::getClientInstance();
    if (!client) {
        return false;
    }

    // Resolving the OreUI provider touches game objects; keep the SEH block
    // free of locals that need C++ unwinding.
    OreUI::FacetRegistryFactory* factory = nullptr;
    __try {
        factory = client->getSceneProvider().mFacetRegistryFactory.get();
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
    if (factory == nullptr) {
        return false;
    }

    auto model = factory->mScreenModelProvider->getMainMenuScreenModel();
    if (!model) {
        return false;
    }
    signedIn = model->isSignedInToXBL();
    return true;
}

} // namespace probe::detail

namespace probe {

void WorldSession::signInOnClient() {
    bool signedIn = false;
    bool const known = detail::queryXblSignedIn(signedIn);
    nlohmann::json out{
        {"event", "signin"                         },
        {"known", known                            },
    };
    if (known) {
        out["signed_in"] = signedIn;
    }
    mOwner.enqueue(out.dump());
}

} // namespace probe
