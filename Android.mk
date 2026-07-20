LOCAL_PATH := $(call my-dir)

ifeq ($(TARGET_DEVICE),dash)
include $(call all-subdir-makefiles,$(LOCAL_PATH))
endif