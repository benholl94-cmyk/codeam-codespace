// settings.gradle.kts — rollout-shield Android app
//
// Project is the dashboard. Single-module app; no transitive deps
// beyond AndroidX + Material Components (standard).
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "rollout-shield"
include(":app")